"""``bash(run_in_background=true)``: the job service's second kind (#611 S2d, ADR-0297).

WHAT THIS MODULE IS. ADR-0293 made background work a job its session owns; the
job service (``aelix_coding_agent/jobs``) runs it, the kernel delivers its
notice once, and a client says what the work is. Delegation is client 1
(``aelix_agents/jobs_client.py``, ADR-0295); this is client 2 (owner,
2026-11-07: "designed together now as the second client to prove the contract;
implemented in the slice after core + delegation"). It registers the ``bash``
kind — :class:`BashKindAdapter` (how a background command's job reads, renders,
pages and recovers) and :class:`BashJobRunner` (one command, spawn to exit) —
and :func:`start_background` is what the ``bash`` tool calls for
``run_in_background``. Everything is behind the job service's one gate
(``jobs/activation.py``): while it is off nothing here is constructed, the tool
lists no ``run_in_background`` and ignores one it is sent, and every ``bash``
call runs as it did before #611.

WHERE IT RUNS (Codex #14). Only on a local backend, and only on a host that says
so: the CLI hands the tool a fail-closed ``background_host`` (true for the TUI,
``-p`` and json, false for RPC — and ACP, which drives aelix over RPC — until
#612), the same way delegation learns it. A tool built with no such option, or
with a remote ``operations`` backend, offers nothing and refuses the call.

THE OUTPUT. The command's stdout and stderr go, as raw bytes, to a file of its
own, ``<tempdir>/aelix-jobs-<user>/<16 hex>.log``, in a 0700 directory checked
for its owner and refused as a symlink, created ``O_EXCL``/``O_NOFOLLOW`` and
0600. A command that started keeps it: aelix never deletes it (as the bash
tool's spill files are not); a refused or failed start removes the file it had
made. Past 1 GiB the rest is read and discarded, with one line in the file
saying so (the contract's number). A drain that cannot prove it read what the
command wrote before it exited says the output may be incomplete, and the file
goes on capturing it (Codex 3). The file's absolute path is in the job's ``start`` fact
(``client_start``), so a restart names it without deriving it again (Codex
#12). The notice carries the end of the output — the last 8 KiB read, cut after
escaping so the whole frame fits 16 KiB — and a footer naming the file and
``job(action="result")``, which pages the file by BYTES: each page ends on a
character boundary, its ``next_offset`` is ``offset + bytes consumed``, and it
always moves forward (r3 §5, Codex #13, N12).

ITS END. Exit 0 is ``done``, any other exit ``failed``, a stop ``stopped`` with
why. On a normal exit what the command left running is RELEASED, as a
top-level ``bash`` call's helpers are (#232; owner, 2026-11-07: "Top-level
released helpers are released as today"): ``normal_exit="release"``. On
Windows that is :meth:`~aelix_ai.utils._process_tree.ProcessTree.preserve_descendants`
on the command's Job, which was created ``kill_on_close=True`` with S2b's
suspended spawn; a Job that cannot be preserved is held until aelix exits and
the job says its helpers are ``unverified``. On Linux the shell gets
``pdeathsig`` with #721's original-parent recheck, and nothing below it does
(owner OQ-C, 2026-11-10): a command's own children can outlive a SIGKILLed
aelix (ADR-0297).

ITS TIME LIMIT (owner OQ-B, 2026-11-10: "무인 실행에만 기본 600초"). In an
unattended run (``-p``, json) a command started without ``timeout`` gets the
bash tool's default (600 s, ``AELIX_BASH_DEFAULT_TIMEOUT``, 0 = none); an
explicit ``timeout`` is honoured up to ``AELIX_BASH_MAX_TIMEOUT``; the TUI
applies no default. A command that reaches its limit is stopped, ends
``stopped``, and its notice says it reached its background time limit.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import dataclasses
import getpass
import logging
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_ai.utils._parent_death import pdeathsig_preexec
from aelix_ai.utils._process_tree import (
    DRAIN_CAP_SECONDS,
    EXIT_DRAIN_SECONDS,
    ProcessTree,
    SuspendedLaunchError,
    _PipeReader,
    _ReadState,
    _retained_handle,
    containment_spawn_kwargs,
)
from aelix_ai.utils._process_walk import group_only_zombies
from aelix_ai.utils._shell import ShellConfig, utf8_output_preamble

from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs.contract import (
    TERMINAL_STATES,
    ClientOutcome,
    HelperCleanup,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    Recovered,
    RelationSpec,
)
from aelix_coding_agent.jobs.notice import escape_body
from aelix_coding_agent.tools import launch_ledger

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

_log = logging.getLogger(__name__)

KIND = "bash"
"""The kind's name, registered by this client — opaque to the service."""
KIND_VERSION = 1
LABEL = "command"
SETTINGS_KEY = "bash"
"""``backgroundJobs.limits.bash`` (ADR-0293 §9)."""
RECORD_TYPE = "aelix.bash_job"
"""This client's own record: ``{v, phase: "exit", status, exit_code, stop_reason, bytes}``."""

CAPABILITIES = KindCapabilities(
    background=True,
    detachable=False,
    output=True,
    # ``job_stop`` stops it, with ``bash``'s permission (owner, 2026-11-08).
    job_tool_control=True,
    control_hint=None,
    counts_foreground=False,
    default_bound=4,
    # ``-p``/json "waits for tracked jobs only" (owner, 2026-11-07): this is one.
    blocks_print_exit=True,
    notify=True,
    # A top-level command's helpers are released at a normal exit (#232).
    normal_exit="release",
    activity="progress",
    noun=("background command", "background commands"),
)

#: The end of the output a notice carries, read as raw bytes (Qwen's
#: ``MAX_NOTIFICATION_OUTPUT_TAIL_BYTES``).
_TAIL_BYTES = 8 * 1024
#: The notice body's budget once escaped, below the 16 KiB frame by its capped
#: parts (as delegation's ``NOTICE_BODY_MAX_BYTES``), so the frame never cuts it.
_BODY_BUDGET = 12 * 1024
#: How much of the command's output its file keeps (the contract's 1 GiB).
_KEEP_BYTES = 1 << 30
#: The command's first line, as the notice shows it.
_COMMAND_HEAD = 200
#: The job's title: the command's first line, shorter, for rows and headers.
_TITLE_HEAD = 80
#: How often the reader tells the service it is still producing output.
_PROGRESS_EVERY = 1.0
#: How long a stopped command's root is given to go once the kill legs ran.
_STOPPED_EXIT_WAIT = 10.0
#: How long the file goes on capturing after a drain that ended without a proof (Codex 3).
_LATE_CAPTURE_SECONDS = 30.0

#: The sink's one write, as a module attribute so a case can fail it without
#: patching ``os.write`` for the whole process.
_write_fd = os.write

_DISCARD_LINE = (
    b"\n[aelix: this command's output passed 1 GiB; the rest was read and discarded]\n"
)

_STOPPED_BY: dict[str, str] = {
    "model": "stopped by you (job_stop)",
    "user": "stopped by the user (/jobs stop)",
    "owner_end": "stopped when its session ended (/new, /resume, /fork or quit)",
    "reload": "stopped by /reload",
    "shutdown": "stopped when aelix exited",
    "signal": "stopped by an interrupt",
    "liveness": "stopped by aelix's liveness check",
}
"""Why a background command was stopped, by the job's stop reason."""

_TIME_LIMIT = "time_limit"

_UNCONFIRMED = (
    "[Its output may be incomplete: 2s after the command ended, aelix could not confirm it had "
    "read everything the command wrote. While aelix runs it goes on reading into the file for up "
    f"to {_LATE_CAPTURE_SECONDS:g}s more; job(action=\"result\") reads what arrived.]"
)
_LEFT_RUNNING = (
    "It exited and left processes running in its process group (a trailing '&', or a daemon it "
    "started): aelix released them, as it releases a foreground command's (#232), so job_stop "
    "does not reach them."
)

REFUSED_HERE = (
    "bash: run_in_background is not available here: this host does not run background "
    "commands (RPC and ACP until #612, a remote shell, or a tool built without the "
    "host's say). Run the command without it."
)
NUL_REFUSED = "bash: the command contains a NUL byte, which no shell can be given. Remove it."
TRAILING_AMPERSAND = (
    "bash: a background command must not end in '&': run_in_background already runs it "
    "in the background. Remove the trailing '&'."
)


# === Where it may run ===============================================================


@dataclass(frozen=True)
class BackgroundOffer:
    """What a ``bash`` tool was built with that lets it start background commands (Codex #14).

    ``host`` answers whether this host runs them now (the CLI's ``background_host``);
    ``unattended`` whether no person watches this run (``None``: unattended).
    ``shell_path`` is the tool's own shell setting.
    """

    host: Callable[[], bool]
    unattended: Callable[[], bool] | None = None
    shell_path: str | None = None

    def allowed(self) -> bool:
        return _answers(self.host)

    def is_unattended(self) -> bool:
        return self.unattended is None or _answers(self.unattended)


def _answers(question: Callable[[], bool]) -> bool:
    """``question()``, fail-closed: a raise is ``False``."""

    try:
        return bool(question())
    except Exception:  # noqa: BLE001 — an unanswerable host offers nothing
        return False


def offer_from(options: Mapping[str, Any]) -> BackgroundOffer | None:
    """The offer a tool's ``options`` make, or ``None`` (fail-closed, read once at build).

    ``None`` unless background jobs are on, the backend is the local one (no
    ``operations``), and a ``background_host`` is given that answers true now.
    """

    from aelix_coding_agent.jobs import activation

    if not activation.is_active() or options.get("operations"):
        return None
    host = options.get("background_host")
    if not callable(host):
        return None
    unattended = options.get("unattended")
    offer = BackgroundOffer(
        host=cast("Callable[[], bool]", host),
        unattended=cast("Callable[[], bool]", unattended) if callable(unattended) else None,
        shell_path=options.get("shell_path"),
    )
    return offer if offer.allowed() else None


def parameter(unattended: bool, default_timeout: float) -> dict[str, Any]:
    """The ``run_in_background`` property the schema lists while it is offered."""

    if unattended and default_timeout > 0:
        limit = (
            f" In this unattended run it is stopped after {_secs(default_timeout)}s unless "
            "you pass a timeout."
        )
    else:
        limit = " It runs until it ends or is stopped unless you pass a timeout."
    return {
        "type": "boolean",
        "description": (
            "Run the command in the background: the call returns a job id at once, the "
            "output goes to a file, and you get a notice when it ends, so do not poll. "
            'job(action="status"|"result", id=…) reads it, job_stop(id=…) stops it. '
            "Do not end the command with '&'." + limit
        ),
    }


def ends_in_ampersand(command: str) -> bool:
    """Whether ``command`` ends in a bare top-level ``&`` (Qwen ``tools/shell.ts``, ``9d1cf39``).

    ``&&``, ``|&``, an escaped ``\\&``, an ``&`` inside quotes, inside ``$(…)``
    or backticks or a subshell's ``(cmd &)``, and one after a comment's ``#``
    are not; ``cmd & # why`` and ``cmd &`` followed by a line continuation are.
    Quotes nested in a substitution are read as the shell reads them (Codex 4).
    What no scan sees — ``eval``, ``bash -c '… &'`` — is reported at the
    command's exit instead (:meth:`BashJobRunner._group_has_members`).
    """

    return _operator_tail(command) == "&"


#: What closes each context the scan can be in: a quote, a substitution, a group.
_CLOSERS = {"'": "'", '"': '"', "`": "`", "$(": ")", "(": ")"}


def _operator_tail(command: str) -> str:
    """The operator characters (``&|;<>``) the command ends in at top level; ``""`` when it ends in a word."""

    tail, stack, escaped, word_start, i = "", [], False, True, 0
    while i < len(command):
        ch = command[i]
        context = stack[-1] if stack else ""
        if escaped:
            escaped = False
            if ch != "\n" and not stack:
                tail = ""  # an escaped character is a word; a line continuation is nothing
        elif context == "'":
            if ch == "'":
                stack.pop()
        elif ch == "\\":
            escaped = True
        elif context and ch == _CLOSERS[context]:
            stack.pop()
        elif context == '"' and ch not in "$`":
            pass
        elif ch == "#" and word_start and context in ("", "$(", "("):
            newline = command.find("\n", i)
            i = len(command) if newline < 0 else newline
            continue
        elif _opens(command, i, context, stack):
            i += len(stack[-1])
            tail = "" if len(stack) == 1 else tail
            word_start = True
            continue
        elif not stack and ch in "&|;<>":
            tail += ch
        elif not stack and not ch.isspace():
            tail = ""
        word_start = ch.isspace() or ch in "&|;<>()"
        i += 1
    # A backslash the input ends on is dropped by the shell (``sh -c 'a &\\'`` runs ``a &``).
    return tail


def _opens(command: str, i: int, context: str, stack: list[str]) -> bool:
    """Push the context ``command[i:]`` opens, if any: a quote, ``$(``, a backtick, or a group inside a substitution."""

    ch = command[i]
    if ch == "$" and command.startswith("(", i + 1):
        stack.append("$(")
    elif ch in "'\"`" and (context != '"' or ch == "`"):
        stack.append(ch)
    elif ch == "(" and context in ("$(", "("):
        stack.append("(")
    else:
        return False
    return True


def resolve_limit(
    timeout_arg: Any, *, default_timeout: float, max_timeout: float, unattended: bool
) -> tuple[float | None, bool]:
    """A background command's time limit, and whether an explicit one was clamped (owner OQ-B).

    An explicit positive ``timeout`` is honoured, clamped to ``max_timeout``
    when that cap is on. Without one, an unattended run gets ``default_timeout``
    (0 = none) and an attended one (the TUI) none at all — this runs BEFORE the
    foreground resolver, whose default would otherwise apply everywhere.
    """

    try:
        requested = float(timeout_arg) if timeout_arg is not None else None
    except (TypeError, ValueError):
        requested = None
    if requested is not None and requested > 0:
        if max_timeout > 0 and requested > max_timeout:
            return max_timeout, True
        return requested, False
    if unattended and default_timeout > 0:
        return default_timeout, False
    return None, False


# === The output file ===================================================================


def _user_tag() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name: the uid, or a fixed word
        getuid = getattr(os, "getuid", None)
        user = str(getuid()) if getuid is not None else "user"
    return re.sub(r"[^A-Za-z0-9._-]", "_", user) or "user"


def output_dir() -> Path:
    """``<tempdir>/aelix-jobs-<user>``: created 0700, refused unless a real directory this user owns."""

    path = Path(tempfile.gettempdir()) / f"aelix-jobs-{_user_tag()}"
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{path} is not a directory (a symlink or a file is never followed)")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise OSError(f"{path} belongs to another user")
    if getuid is not None and info.st_mode & 0o077:
        raise OSError(f"{path} is open to other users (mode {oct(info.st_mode & 0o777)})")
    return path


def create_output_file() -> str:
    """A new, empty output file (``O_EXCL``, 0600); its absolute path."""

    folder = output_dir()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    path = folder / f"{secrets.token_hex(8)}.log"
    os.close(os.open(path, flags, 0o600))
    return str(path.resolve())


class _Sink:
    """Where the reader thread puts the command's bytes: the file, and the last 8 KiB.

    ``write`` runs on the reader thread and never raises (a raise there would end
    the read, and the command would meet a closed pipe). Past :data:`_KEEP_BYTES`
    the bytes are counted and dropped, after one line in the file says so.
    """

    def __init__(self, path: str, *, on_bytes: Callable[[int], None] | None = None) -> None:
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        self.path = path
        self._fd: int | None = os.open(path, flags)
        self._lock = threading.Lock()
        self._tail = bytearray()
        self._on_bytes = on_bytes
        self.total = 0
        self.kept = 0
        self.discarded = 0
        self.write_error: str | None = None

    def write(self, chunk: bytes) -> None:
        with self._lock:
            self.total += len(chunk)
            self._tail += chunk
            if len(self._tail) > 2 * _TAIL_BYTES:
                del self._tail[: len(self._tail) - _TAIL_BYTES]
            room = _KEEP_BYTES - self.kept
            keep = chunk[: max(0, room)]
            self.discarded += len(chunk) - len(keep)
            if keep:
                self._append(keep)
                self.kept += len(keep)
            if len(keep) < len(chunk) and self.discarded == len(chunk) - len(keep):
                self._append(_DISCARD_LINE)
            total = self.total
        if self._on_bytes is not None:
            self._on_bytes(total)

    def _append(self, data: bytes) -> None:
        fd = self._fd
        if fd is None or self.write_error is not None:
            return
        view = memoryview(data)
        try:
            while view:
                view = view[_write_fd(fd, view) :]
        except OSError as exc:
            self.write_error = f"{type(exc).__name__}: {exc}"

    def tail(self) -> bytes:
        with self._lock:
            return bytes(self._tail[-_TAIL_BYTES:])

    def summary(self) -> _SinkSummary:
        """What the outcome reads of it, kept once the sink itself is let go."""

        with self._lock:
            tail = bytes(self._tail[-_TAIL_BYTES:])
            return _SinkSummary(self.total, self.discarded, self.write_error, tail)

    def close(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
            self._on_bytes = None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


@dataclass(frozen=True)
class _SinkSummary:
    """A closed sink's numbers and tail: what a settled job keeps instead of the sink and its lock."""

    total: int
    discarded: int
    write_error: str | None
    raw_tail: bytes

    def tail(self) -> bytes:
        return self.raw_tail


# === The process the job holds ===========================================================


class _ShellProcess:
    """The ``Popen`` the runner spawned, as the job's evidence reads a process.

    ``returncode`` is set by the runner's own waiter thread, the one place that
    reaps it; ``wait`` waits for that thread without blocking the loop. Once
    reaped it keeps only the exit code (:meth:`reaped`): the ``Popen`` — and on
    win32 its process handle — is not held for the service's lifetime (Codex 7).
    """

    def __init__(self, popen: subprocess.Popen[bytes], exited: asyncio.Event) -> None:
        self._popen: subprocess.Popen[bytes] | None = popen
        self._exited = exited
        self._code: int | None = None
        self.pid = popen.pid

    @property
    def returncode(self) -> int | None:
        popen = self._popen
        return popen.returncode if popen is not None else self._code

    async def wait(self) -> int | None:
        await self._exited.wait()
        return self.returncode

    def kill(self) -> None:
        popen = self._popen
        if popen is not None:
            popen.kill()

    def reaped(self, code: int | None) -> None:
        self._code, self._popen = code, None


def _close_process_handle(proc: Any) -> None:
    """Close ``Popen``'s own win32 process handle once it is reaped (Codex 7); nothing elsewhere.

    The tree attached with it only at its spawn (a suspended one took its own
    copy), so after the reap nothing reads it. CPython would close it only when
    the ``Popen`` is collected.
    """

    close = getattr(getattr(proc, "_handle", None), "Close", None)
    if callable(close):
        with contextlib.suppress(OSError):
            close()


def _spawn_kwargs(*, suspended: bool) -> dict[str, Any]:
    """A contained spawn; win32 adds ``CREATE_SUSPENDED`` when it can resume, Linux the shell's pdeathsig."""

    kwargs = containment_spawn_kwargs(new_session=True)
    if suspended:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | ProcessTree.suspend_flags()
    if sys.platform.startswith("linux"):
        # On the shell only (owner OQ-C): what the command starts below it may
        # outlive a SIGKILLed aelix (ADR-0297).
        kwargs["preexec_fn"] = pdeathsig_preexec()
    return kwargs


def _popen_background(
    shell: ShellConfig, command: str, cwd: str, env: dict[str, str], *, suspended: bool
) -> subprocess.Popen[bytes]:
    """The background shell: ``tools/bash.py``'s ``_popen_shell`` with the background spawn's kwargs."""

    return cast(
        "subprocess.Popen[bytes]",
        subprocess.Popen(  # noqa: S603
            [shell.path, shell.command_flag, utf8_output_preamble(shell.path) + command],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **_spawn_kwargs(suspended=suspended),
        ),
    )


#: Trees whose helpers could not be released (a failed preserve): held, never
#: closed, so their Job ends them only when aelix exits (r2 §5, Codex #10).
_HELD_FOR_LIFE: list[ProcessTree] = []


# === The runner ============================================================================


@dataclass(frozen=True)
class Launch:
    """Everything one background command needs, read in the ``bash`` call."""

    command: str
    shell: ShellConfig
    cwd: str
    env: dict[str, str]
    output: str
    timeout: float | None
    command_head: str


class BashJobRunner:
    """One background command, from spawn to exit (ADR-0293 :class:`~aelix_coding_agent.jobs.contract.JobRunner`)."""

    def __init__(self, launch: Launch) -> None:
        self._launch = launch
        self._ctx: Any = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._record: Any = None
        self._tree: ProcessTree | None = None
        self._sink: _Sink | None = None
        self._summary = _SinkSummary(0, 0, None, b"")
        self._reader: _PipeReader | None = None
        self._state: _ReadState | None = None
        self._exited: asyncio.Event | None = None
        self._eof: asyncio.Event | None = None
        self._exited_at: float | None = None
        self._stop: asyncio.Event | None = None
        self._stopped_by: str | None = None
        self._finish: asyncio.Task[None] | None = None
        self._last_progress = 0.0
        self._shell: _ShellProcess | None = None
        self._returncode: int | None = None
        self._waiting = False
        self._reading = False
        #: The launch failed after something was acquired: its tree is closed,
        #: never preserved (Codex 6).
        self._aborted = False
        #: The drain ended without a proof that it read what the command wrote
        #: before it exited (Codex 3): the outcome says so.
        self._unconfirmed = False
        self._late: asyncio.Task[None] | None = None
        self._left_running = False

    # ── run ──────────────────────────────────────────────────────

    async def run(self, ctx: Any) -> ClientOutcome:
        self._ctx = ctx
        loop = asyncio.get_running_loop()
        self._exited, self._eof, self._stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
        if ctx.stop_latched:
            ctx.launch_failed("it was stopped before it started")
            return ClientOutcome("stopped", "stopped before it started")
        try:
            self._spawn(ctx, loop)
        except Exception as exc:  # noqa: BLE001 — every launch failure gets the same undoing
            # Undone BEFORE it is reported (Codex 1, 2): the process stopped and
            # reaped through the job's legs, its pipe and file closed. The
            # service's barrier then confirms it, and its slot goes back once.
            await self._abort_launch()
            why = str(exc) if isinstance(exc, SuspendedLaunchError) else f"{type(exc).__name__}: {exc}"
            ctx.launch_failed(f"the command could not be started: {why}")
            return ClientOutcome("failed", "it could not be started")
        ctx.launched()
        try:
            await self._wait_for_the_end()
        finally:
            self._finish = asyncio.ensure_future(self._drain_and_close())
        await asyncio.shield(self._finish)
        self._left_running = self._stopped_by is None and self._group_has_members()
        if self._returncode is not None:
            self._proc = None  # reaped: only its exit code is kept (Codex 7)
        return self._outcome()

    def _spawn(self, ctx: Any, loop: asyncio.AbstractEventLoop) -> None:
        launch = self._launch
        # The file first: one that cannot be opened fails the launch before a
        # process exists.
        self._sink = _Sink(launch.output, on_bytes=self._progress_poster(loop))
        suspended = ProcessTree.suspend_flags() != 0
        # Through ``tools/bash.py``'s ``_spawn_shell``, so an armed delegated
        # agent's launch ledger records the shell's group (ADR-0296).
        from aelix_coding_agent.tools.bash import _spawn_shell

        proc = _spawn_shell(
            launch.shell,
            launch.command,
            launch.cwd,
            launch.env,
            popen=lambda *a: _popen_background(*a, suspended=suspended),
        )
        self._proc = proc
        try:
            # Before any await (ADR-0238 §B.4): on win32 the suspended process is
            # assigned to its Job, the stop latch read, and only then resumed.
            tree = ProcessTree.attach(
                proc.pid,
                handle=_retained_handle(proc),
                kill_on_close=True,
                suspended=suspended,
                stop_latched=lambda: bool(ctx.stop_latched),
            )
        except SuspendedLaunchError as exc:
            if exc.tree is not None:
                # Not ended: the tree still holds it, and the barrier decides
                # (Codex 6: this runner owns it, as an aborted launch).
                self._tree = exc.tree
                self._record = ctx.register_process(proc.pid, tree=exc.tree)
            raise
        self._tree = tree
        exited = self._exited
        assert exited is not None
        self._shell = _ShellProcess(proc, exited)
        self._record = ctx.register_process(
            self._shell, pgid=proc.pid if sys.platform != "win32" else None, tree=tree
        )
        self._start_waiter(proc, loop)
        self._start_reading(proc, loop)

    def _start_waiter(self, proc: subprocess.Popen[bytes], loop: asyncio.AbstractEventLoop) -> None:
        threading.Thread(
            target=self._wait_root, args=(proc, loop), daemon=True, name=f"aelix-bash-job-{proc.pid}"
        ).start()
        self._waiting = True

    async def _abort_launch(self) -> None:
        """Undo a launch that failed after its first acquisition (Codex 1, 2).

        The process, when one exists, is stopped through the job's evidence
        legs once it is registered (S2a), and reaped here when no waiter thread
        runs; the pipe no reader took and the output file are closed; the tree is
        marked aborted, so the cleanup closes it instead of preserving it.
        """

        self._aborted = True
        proc = self._proc
        if proc is not None:
            if self._record is not None:
                ev.kill_now(self._record)
            elif self._tree is not None:
                self._tree.hard_kill()
            with contextlib.suppress(OSError):
                proc.kill()
            if not self._reading:
                self._release_pipe(proc)
            await self._reaped(proc)
        self._close_sink()

    async def _reaped(self, proc: subprocess.Popen[bytes]) -> None:
        """The aborted launch's root, reaped: by the waiter thread when it runs, else here (bounded)."""

        exited = self._exited
        if not self._waiting:
            await asyncio.to_thread(self._reap, proc)
            if exited is not None:
                exited.set()
        elif exited is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(exited.wait(), timeout=_STOPPED_EXIT_WAIT)

    def _reap(self, proc: subprocess.Popen[bytes]) -> None:
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            proc.wait(timeout=_STOPPED_EXIT_WAIT)
        self._settle_root(proc)

    def _settle_root(self, proc: subprocess.Popen[bytes]) -> None:
        """After the reap: the exit code kept, the ``Popen`` and its win32 handle let go (Codex 7)."""

        self._returncode = proc.returncode
        self._exited_at = time.monotonic()
        if proc.returncode is None:
            return  # not reaped: the barrier reads it as alive
        if self._shell is not None:
            self._shell.reaped(proc.returncode)
        _close_process_handle(proc)

    @staticmethod
    def _release_pipe(proc: subprocess.Popen[bytes]) -> None:
        if proc.stdout is not None:
            with contextlib.suppress(OSError):
                proc.stdout.close()

    def _progress_poster(self, loop: asyncio.AbstractEventLoop) -> Callable[[int], None]:
        """The sink's byte count, to the job's progress on the loop, at most once a second."""

        def on_bytes(total: int) -> None:
            now = time.monotonic()
            if now - self._last_progress >= _PROGRESS_EVERY:
                self._last_progress = now
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._progress, total)

        return on_bytes

    def _start_reading(self, proc: subprocess.Popen[bytes], loop: asyncio.AbstractEventLoop) -> None:
        eof, sink = self._eof, self._sink
        assert eof is not None and sink is not None and proc.stdout is not None

        def at_eof() -> None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(eof.set)

        self._state = _ReadState(last_chunk_at=time.monotonic())
        self._reader = _PipeReader(
            proc.stdout, self._state, on_chunk=sink.write, on_eof=at_eof, sole_reader=True
        )
        self._reader.start()
        self._reading = True

    def _close_sink(self) -> None:
        """Close the file and let the sink go, keeping its numbers.

        A settled job is kept by the service for the rest of the process: what
        it holds must not hold an OS handle. On win32 CPython 3.12 and older
        every lock is a kernel semaphore, the sink's included (#760 CI).
        """

        sink, self._sink = self._sink, None
        if sink is not None:
            sink.close()
            self._summary = sink.summary()

    def _progress(self, total: int) -> None:
        with contextlib.suppress(Exception):
            self._ctx.progress({"bytes": total})

    def _wait_root(self, proc: subprocess.Popen[bytes], loop: asyncio.AbstractEventLoop) -> None:
        """The waiter thread: the one place the shell is reaped. Daemon: never holds aelix's exit."""

        with contextlib.suppress(Exception):
            proc.wait()
        self._settle_root(proc)
        if self._reader is not None and self._exited_at is not None:
            self._reader.pin_after(self._exited_at)
        exited = self._exited
        if exited is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(exited.set)

    async def _wait_for_the_end(self) -> None:
        """Until the shell exits: on its own, or after a stop or its time limit."""

        exited, stop = self._exited, self._stop
        assert exited is not None and stop is not None
        waits = {asyncio.ensure_future(exited.wait()), asyncio.ensure_future(stop.wait())}
        try:
            await asyncio.wait(waits, timeout=self._launch.timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waits:
                waiter.cancel()
        if exited.is_set():
            return
        if self._stopped_by is None:
            self._stopped_by = _TIME_LIMIT
        if self._record is not None:
            await ev.stop_legs([self._record])
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(exited.wait(), timeout=_STOPPED_EXIT_WAIT)

    async def _drain_and_close(self) -> None:
        """#232's idle drain after the exit, then the file closed. The read goes on, keeping nothing."""

        reader, eof, state = self._reader, self._eof, self._state
        if reader is None or eof is None or state is None:
            return
        exited = self._exited
        if exited is not None and not exited.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(exited.wait(), timeout=_STOPPED_EXIT_WAIT)
        armed = self._exited_at or time.monotonic()
        reader.pin_after(armed)
        hard = armed + DRAIN_CAP_SECONDS
        while not eof.is_set() and reader.is_alive():
            now = time.monotonic()
            idle_end = max(state.last_chunk_at, armed) + EXIT_DRAIN_SECONDS
            if now >= hard or (now >= idle_end and reader.proven()):
                break
            due = min(hard, idle_end if idle_end > now else now + EXIT_DRAIN_SECONDS)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(eof.wait(), max(0.005, due - now))
        if self._read_all(reader, eof):
            self._end_capture(reader)
            return
        # The hard cap with no proof (Codex 3): what the command wrote before it
        # exited may still be in the pipe or in a starved reader. The outcome
        # says so, and the file keeps capturing until a proof, EOF or the bound.
        self._unconfirmed = True
        self._late = asyncio.ensure_future(self._capture_late(reader, eof))
        self._late.add_done_callback(_log_late_failure)

    @staticmethod
    def _read_all(reader: _PipeReader, eof: asyncio.Event) -> bool:
        return eof.is_set() or not reader.is_alive() or reader.proven()

    async def _capture_late(self, reader: _PipeReader, eof: asyncio.Event) -> None:
        end = time.monotonic() + _LATE_CAPTURE_SECONDS
        try:
            while not self._read_all(reader, eof) and time.monotonic() < end:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(eof.wait(), 0.1)
        finally:
            self._end_capture(reader)

    def _end_capture(self, reader: _PipeReader) -> None:
        reader.detach()
        self._close_sink()
        # An armed delegated agent retires the group this command left empty
        # (ADR-0296); a no-op in every unarmed process.
        launch_ledger.after_call()
        # The reader thread reads on, keeping nothing, until its pipe closes; the
        # job does not keep it (#760 CI): the thread object, its stream and its
        # held pipe are each a lock — a kernel semaphore on win32 CPython 3.12
        # and older — and on 3.13 and later an unjoined thread keeps its handle
        # while the object lives.
        self._reader = self._state = None

    def _group_has_members(self) -> bool:
        """Whether the command, ended on its own, left processes in its group (win32: its Job).

        What a trailing-``&`` scan cannot see — ``eval``, ``bash -c '… &'``, a
        daemon — is released at the exit as a foreground command's helpers are
        (#232), and the notice says so. Read once, after the drain; a helper that
        left the group (``setsid``) is not seen.
        """

        tree = self._tree
        if tree is None or tree.closed:
            return False
        if sys.platform == "win32":
            return bool(tree.active_processes())
        pgid = tree.pid
        try:
            os.killpg(pgid, 0)  # pyright: ignore[reportAttributeAccessIssue]
        except OSError:
            return False
        return not group_only_zombies(pgid)

    def _outcome(self) -> ClientOutcome:
        sink = self._sink if self._sink is not None else self._summary
        code = self._returncode
        stopped_by = self._stopped_by
        status: Literal["done", "failed", "stopped"]
        if stopped_by is not None:
            status = "stopped"
        elif code == 0:
            status = "done"
        else:
            status = "failed"
        payload = {
            "exit_code": code,
            "stop_reason": stopped_by,
            "bytes": sink.total,
            "discarded": sink.discarded,
            "tail": _sink_tail(sink),
            "command": self._launch.command_head,
            "output": self._launch.output,
            "timeout": self._launch.timeout,
            "write_error": sink.write_error,
            "job_id": getattr(self._ctx, "job_id", None),
            "output_unconfirmed": self._unconfirmed,
            "left_running": self._left_running,
        }
        return ClientOutcome(status=status, brief=_brief(payload), payload=payload)

    # ── the rest of the runner contract ─────────────────────────────

    def request_stop(self, reason: str) -> None:
        if self._stopped_by is None:
            self._stopped_by = reason
        if self._stop is not None:
            self._stop.set()

    async def settle(self) -> None:
        if self._finish is not None:
            await asyncio.shield(self._finish)
        elif self._reader is not None:
            self._finish = asyncio.ensure_future(self._drain_and_close())
            await asyncio.shield(self._finish)

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        """Release or close the command's tree once the barrier confirmed its root gone (#232, r2 §5).

        A normal exit RELEASES what the command left running. On win32 that is
        ``preserve_descendants`` on its Job, then the close; a Job that cannot
        be preserved is held for aelix's lifetime and the answer is
        ``unverified``. A stop closes the tree its legs already ended.
        """

        tree = self._tree
        if tree is None:
            return HelperCleanup("none")
        if not receipt.get("confirmed"):
            return HelperCleanup("unverified", "its processes are not yet confirmed gone")
        released = receipt.get("policy") == "release" and self._stopped_by is None and not self._aborted
        if released and tree.job is not None and not tree.preserve_descendants():
            _HELD_FOR_LIFE.append(tree)
            self._let_go_of_tree()
            return HelperCleanup(
                "unverified", "Windows could not release its helpers; they end when aelix exits"
            )
        tree.close()
        self._let_go_of_tree()
        return HelperCleanup("none", "released (#232)" if released else None)

    def _let_go_of_tree(self) -> None:
        """The settled job keeps no tree: closed, or held by ``_HELD_FOR_LIFE`` (#760 CI).

        Its record is the service's, the same object the barrier read, and the
        barrier is done with it; its lock is a kernel semaphore on win32 CPython
        3.12 and older.
        """

        self._tree = None
        if self._record is not None:
            self._record.tree = None

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        payload = outcome.payload
        return [
            (
                RECORD_TYPE,
                {
                    "v": 1,
                    "phase": "exit",
                    "status": outcome.status,
                    "exit_code": payload.get("exit_code"),
                    "stop_reason": payload.get("stop_reason"),
                    "bytes": payload.get("bytes"),
                    "output_unconfirmed": bool(payload.get("output_unconfirmed")),
                },
            )
        ]


# === The kind's adapter ===================================================================


class BashKindAdapter:
    """How the job service reads, renders, pages and recovers a background command."""

    name = KIND
    version = KIND_VERSION
    label = LABEL
    settings_key = SETTINGS_KEY
    capabilities = CAPABILITIES

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        """A restart's reading of an ENDED job: its outcome fact and its own exit record."""

        fact = client.get("fact")
        fact = fact if isinstance(fact, Mapping) else {}
        record = _exit_record(client.get("records"))
        status = fact.get("status")
        return ClientOutcome(
            status=status if status in ("done", "failed", "stopped") else "failed",
            brief=str(fact.get("brief", "")),
            payload=_payload_of(record),
        )

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        payload = outcome.payload
        head = [_status_line(outcome.status, payload)]
        command = payload.get("command")
        if isinstance(command, str) and command:
            head.append(f"$ {command}")
        total = payload.get("bytes")
        tail = payload.get("tail")
        body = "\n".join(head)
        if isinstance(tail, str) and tail:
            room = _BODY_BUDGET - len(escape_body(body).encode("utf-8")) - 128
            kept, cut = _tail_within(tail, room)
            if cut or (isinstance(total, int) and total > _TAIL_BYTES):
                body += f"\n(the end of its output; {total} bytes in all):"
            body += f"\n{kept}"
        elif total == 0 and not payload.get("output_unconfirmed"):
            body += "\n(no output)"
        return NoticeBody(body=body + _caveats(payload), next_action=_footer(payload))

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        return None

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        rows = [f"{snapshot.label} · {snapshot.title}"[: max(1, width)]]
        progress = snapshot.progress or {}
        total = snapshot.payload.get("bytes", progress.get("bytes"))
        path = snapshot.client_start.get("output")
        if isinstance(path, str):
            size = f"{total} bytes" if isinstance(total, int) else "output"
            rows.append(f"  {size} → {path}"[: max(1, width)])
        return rows

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        path = job.client_start.get("output")
        if not isinstance(path, str) or not path:
            return None
        page = page_file(path, offset=offset, limit=limit, ended=job.state in TERMINAL_STATES)
        if job.payload.get("output_unconfirmed") and not page.more:
            page = dataclasses.replace(page, text=f"{page.text}\n{_UNCONFIRMED}")
        return page

    async def recover(
        self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]
    ) -> Recovered | None:
        """A restart's verdict on a command started and not ended: its exit record, or the OS's evidence.

        The exit record is written by the service before the job's ``cleanup``
        and ``outcome`` (Codex #12), so a crash between them still reads how it
        ended — ``stopped`` with why, or the exit code of one killed from
        outside. Without one, ``None``: the service classifies from OS evidence
        (``unresolved`` while the shell lives, ``lost`` once gone) and never
        signals it.
        """

        record = _exit_record(client_records)
        if record is None:
            return None
        status = record.get("status")
        if status not in ("done", "failed", "stopped"):
            return None
        payload = {**_launch_of(facts), **_payload_of(record)}
        return Recovered(
            status=status,
            brief=f"recorded {_brief(payload) or status} before aelix restarted",
            payload=payload,
        )

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        # The shell may ``exec`` the command, so its argv proves nothing: the
        # recorded pid and start time decide.
        return RelationSpec()

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        client = facts.get("client")
        path = client.get("output") if isinstance(client, Mapping) else None
        if not isinstance(path, str) or not path:
            return None
        return f"Its output, as far as it got, is in {path}."


# === Paging ==================================================================================


def page_file(path: str, *, offset: int, limit: int, ended: bool) -> OutputSlice:
    """One page of the output file, by bytes (r3 §5, Codex #13, N12).

    ``next_offset = offset + bytes consumed``, and a page with bytes after
    ``offset`` consumes at least one. A page never ends inside a character: it
    stops before one its ``limit`` would split, and when that would consume
    nothing it reads up to three more bytes to complete it. Invalid bytes are
    consumed as U+FFFD. An incomplete character at the end of the file is
    consumed as U+FFFD once the job has ended (the end is final), and left
    unconsumed while it runs: the page is the text before it, or ``""``, and
    ``more`` is false when that character is all that follows. Otherwise
    ``more`` says whether bytes exist at or past ``next_offset``.
    """

    start = max(0, int(offset))
    want = max(1, int(limit))
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(start)
            data = fh.read(want + 3)
    except OSError as exc:
        return OutputSlice(text=f"its output file cannot be read: {exc}", offset=start, next_offset=start)
    text, used, held = _decode_page(data, want, at_eof=start + len(data) >= size, ended=ended)
    following = start + used
    return OutputSlice(
        text=text,
        offset=start,
        total=size,
        more=following < size and not held,
        next_offset=following,
    )


def _decode(data: bytes, *, final: bool = False) -> tuple[str, int]:
    """``data`` decoded as UTF-8 with U+FFFD for what is invalid, and how many trailing bytes wait for more."""

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = decoder.decode(data, final=final)
    return text, len(decoder.getstate()[0])


def _decode_page(data: bytes, want: int, *, at_eof: bool, ended: bool) -> tuple[str, int, bool]:
    """The page's text, the bytes it consumed, and whether what follows is only a running job's incomplete tail."""

    window = data[:want]
    text, pending = _decode(window)
    if not pending:
        return text, len(window), False
    used = len(window) - pending
    tail = at_eof and len(data) == len(window)  # the split character ends the file
    if used > 0:
        if tail and ended:
            return _decode(window, final=True)[0], len(window), False
        return text, used, tail and not ended
    # A limit smaller than the first character: read on until it resolves,
    # complete or replaced, and consume only what resolved it.
    for size in range(len(window) + 1, len(data) + 1):
        first, waiting = _decode(data[:size])
        if first:
            return first, size - waiting, False
    if at_eof and not ended:
        return "", 0, True
    return _decode(data, final=True)[0], len(data), False


def _tail_within(tail: str, room: int) -> tuple[str, bool]:
    """The end of ``tail``, escaped as the frame will, within ``room`` bytes; and whether it was cut."""

    safe = escape_body(tail)
    raw = safe.encode("utf-8")
    if len(raw) <= room:
        return safe, False
    # Never start inside a character: the bytes cut off at the front go.
    return raw[len(raw) - max(0, room) :].decode("utf-8", errors="ignore"), True


# === Helpers ===================================================================================


def _sink_tail(sink: _Sink | _SinkSummary | None) -> str:
    if sink is None:
        return ""
    raw = sink.tail()
    return _decode_tail(raw, cut=sink.total > len(raw))


def _decode_tail(raw: bytes, *, cut: bool) -> str:
    """The last bytes read, as text, U+FFFD for what is not UTF-8.

    Only when the tail was CUT from a longer output is a character the cut
    split at its front dropped; an uncut output keeps every byte (Codex 8).
    """

    skip = 0
    while cut and skip < min(3, len(raw)) and (raw[skip] & 0xC0) == 0x80:
        skip += 1
    return raw[skip:].decode("utf-8", errors="replace")


def _caveats(payload: Mapping[str, Any]) -> str:
    """What the notice adds after the output: an unconfirmed end, and what the command left running."""

    lines = [_UNCONFIRMED] if payload.get("output_unconfirmed") else []
    if payload.get("left_running"):
        lines.append(_LEFT_RUNNING)
    return "".join(f"\n{line}" for line in lines)


def _log_late_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        _log.warning("background bash: the late capture failed: %r", task.exception())


def _secs(value: float) -> str:
    return f"{value:g}"


def _status_line(status: str, payload: Mapping[str, Any]) -> str:
    code = payload.get("exit_code")
    reason = payload.get("stop_reason")
    if status == "stopped":
        if reason == _TIME_LIMIT:
            limit = payload.get("timeout")
            within = f" ({_secs(limit)}s)" if isinstance(limit, int | float) else ""
            return f"It was stopped after reaching its background time limit{within}."
        return f"It was {_STOPPED_BY.get(str(reason), 'stopped')}."
    if isinstance(code, int) and code < 0 and sys.platform != "win32":
        return f"It was killed by signal {-code}, not by aelix."
    if isinstance(code, int):
        return f"It exited with code {code}."
    return "It ended; its exit code is unknown."


def _brief(payload: Mapping[str, Any]) -> str:
    reason = payload.get("stop_reason")
    if reason == _TIME_LIMIT:
        return "stopped after reaching its background time limit"
    if isinstance(reason, str):
        return _STOPPED_BY.get(reason, "stopped")
    code = payload.get("exit_code")
    if isinstance(code, int) and code < 0 and sys.platform != "win32":
        return f"killed by signal {-code}"
    return f"exit code {code}" if isinstance(code, int) else ""


def _footer(payload: Mapping[str, Any]) -> str | None:
    path = payload.get("output")
    if not isinstance(path, str) or not path:
        return None
    total = payload.get("bytes")
    size = f" ({total} bytes)" if isinstance(total, int) else ""
    job_id = payload.get("job_id")
    reader = (
        f'job(action="result", id="{job_id}") reads it in pages.'
        if isinstance(job_id, str) and job_id
        else 'job(action="result") reads it in pages.'
    )
    extra = ""
    if isinstance(payload.get("discarded"), int) and payload["discarded"] > 0:
        extra = f" Past 1 GiB, {payload['discarded']} bytes were discarded."
    if isinstance(payload.get("write_error"), str):
        extra += f" Writing the file failed ({payload['write_error']}); it holds less than the command wrote."
    return f"Full output: {path}{size}; {reader}{extra}"


def _exit_record(records: Any) -> Mapping[str, Any] | None:
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if (
            isinstance(record, Mapping)
            and record.get("custom_type") == RECORD_TYPE
            and record.get("phase") == "exit"
        ):
            return record
    return None


def _payload_of(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {}
    return {
        "exit_code": record.get("exit_code"),
        "stop_reason": record.get("stop_reason"),
        "bytes": record.get("bytes"),
        **({"output_unconfirmed": True} if record.get("output_unconfirmed") else {}),
    }


def _launch_of(facts: Mapping[str, Any]) -> dict[str, Any]:
    """What the job's ``start`` fact recorded of its launch, as a notice reads it (Codex 5)."""

    client = facts.get("client")
    client = client if isinstance(client, Mapping) else {}
    launch: dict[str, Any] = {
        "output": client.get("output"),
        "command": client.get("command_head"),
        "timeout": client.get("timeout"),
    }
    if isinstance(facts.get("job_id"), str):
        launch["job_id"] = facts["job_id"]
    return launch


# === The tool's side =========================================================================


async def start_background(
    service: JobService,
    *,
    command: str,
    shown: str,
    shell: ShellConfig,
    cwd: str,
    env: dict[str, str],
    timeout: float | None,
    cap: float | None = None,
) -> ToolResult:
    """Start one background command as a job of the current session; the call's answer.

    ``command`` is what the shell runs (an embedder's prefix included); ``shown``
    is the model's own command, which the title and the notice's ``$`` line name
    (verify F8). ``cap`` is the maximum a larger ``timeout`` was clamped to.
    """

    from aelix_coding_agent.jobs.service import JobLaunchFailed, JobStartRefused

    head = _first_line(shown, _COMMAND_HEAD)
    try:
        output = create_output_file()
    except OSError as exc:
        return _error(f"bash: the background command's output file could not be created: {exc}")
    launch = Launch(
        command=command,
        shell=shell,
        cwd=cwd,
        env=env,
        output=output,
        timeout=timeout,
        command_head=head,
    )
    try:
        job_id = await service.start(
            KIND,
            title=_first_line(shown, _TITLE_HEAD),
            params={"launch": launch},
            client_start={
                "v": 1,
                "output": output,
                "command_head": head,
                "cwd": cwd,
                "timeout": timeout,
            },
        )
    except (JobStartRefused, JobLaunchFailed) as exc:
        with contextlib.suppress(OSError):
            os.unlink(output)
        return _error(f"bash: the background command was not started: {exc}")
    limit = (
        f" It is stopped after {_secs(timeout)}s if it has not ended."
        if timeout is not None
        else ""
    )
    if cap is not None:
        # As the foreground answer says it (verify F9).
        limit += (
            f" The requested timeout exceeded the {_secs(cap)}s cap; raise "
            "AELIX_BASH_MAX_TIMEOUT to allow longer."
        )
    return ToolResult(
        content=[
            TextContent(
                text=(
                    f"Started in the background as job {job_id}; its output goes to {output}."
                    f"{limit} A notice comes when it ends, so do not poll: "
                    f'job(action="status"|"result", id="{job_id}") reads it, '
                    f'job_stop(id="{job_id}") stops it.'
                )
            )
        ],
        is_error=False,
    )


def _first_line(command: str, limit: int) -> str:
    line = command.strip().splitlines()[0] if command.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _error(text: str) -> ToolResult:
    return ToolResult(content=[TextContent(text=text)], is_error=True)


def register(service: JobService) -> None:
    """Register the ``bash`` kind on the process's job service (``cli/entry.py``, while jobs are on)."""

    service.register_kind(BashKindAdapter(), lambda params: BashJobRunner(params["launch"]))


__all__ = [
    "CAPABILITIES",
    "KIND",
    "RECORD_TYPE",
    "NUL_REFUSED",
    "REFUSED_HERE",
    "TRAILING_AMPERSAND",
    "BackgroundOffer",
    "BashJobRunner",
    "BashKindAdapter",
    "Launch",
    "create_output_file",
    "ends_in_ampersand",
    "offer_from",
    "output_dir",
    "page_file",
    "parameter",
    "register",
    "resolve_limit",
    "start_background",
]
