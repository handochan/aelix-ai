"""bash tool — Pi parity ``coding-agent/src/core/tools/bash.ts``.

Sequential execution_mode. ``BashOperations`` is the swap surface for
remote execution (e.g. SSH) — local default via
:func:`create_local_bash_operations`.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.utils._process_tree import (
    EXIT_DRAIN_SECONDS,
    INTERRUPT_REAP_SECONDS,
    KILL_DRAIN_SECONDS,
    REAP_GRACE_SECONDS,
    ProcessTree,
    _end_the_tree,
    _PipeReader,
    _ReadState,
    _retained_handle,
    containment_spawn_kwargs,
)

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


# How each shell family takes a command STRING. POSIX shells use ``-c``;
# ``cmd.exe`` accepts only ``/c``; PowerShell is spelled ``-Command`` in full
# (``powershell.exe`` has other ``-C…`` parameters, so the abbreviation is not
# reliably unambiguous across 5.1 and 7).
_POSIX_COMMAND_FLAG = "-c"
_CMD_COMMAND_FLAG = "/c"
_POWERSHELL_COMMAND_FLAG = "-Command"

_POWERSHELL_NAMES = frozenset({"pwsh", "powershell"})
_CMD_NAMES = frozenset({"cmd", "command"})


# A trailing version on a shell's filename: ``bash-5.2``, ``zsh-5.9``,
# ``ksh93``, ``bash-5.2p26``. Anchored on a DIGIT, so it can only ever shorten
# a name to a shorter one — it cannot invent a match. Removing it is what keeps
# a version-suffixed genuine bash classifiable; see :func:`shell_basename`.
_VERSION_SUFFIX_RE = re.compile(r"[-_]?\d[\d.]*[a-z]*\d*$")


def shell_basename(shell: str) -> str:
    """Canonical shell name from a shell path.

    Lower-cased basename with a ``.exe`` extension and any trailing version
    suffix removed, so ``/usr/local/bin/bash-5.2`` and ``ksh93`` answer to
    ``bash`` and ``ksh``.

    Splits on BOTH separators rather than deferring to :mod:`os.path`, because
    the caller may be reasoning about a Windows path while running on POSIX
    (the permission gate, and the tests that drive it) — ``posixpath.basename``
    would hand back the whole ``C:\\…\\powershell.exe`` string.

    Stripping the version matters for more than tidiness: a distro or Homebrew
    ``bash-5.2`` on ``$SHELL`` would otherwise fail to match ``bash`` and the
    AUTO-mode gate would prompt for every command a real bash was about to run.
    The strip cannot go the other way and wrongly ADMIT a shell — it only ever
    maps a name to a shorter one, and ``fish-3.6`` still resolves to ``fish``,
    which stays out of the classifiable set.
    """

    name = shell.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return _VERSION_SUFFIX_RE.sub("", name) or name


def _command_flag_for(shell: str) -> str:
    """The ``run this command string`` flag for ``shell``."""

    name = shell_basename(shell)
    if name in _POWERSHELL_NAMES:
        return _POWERSHELL_COMMAND_FLAG
    if name in _CMD_NAMES:
        return _CMD_COMMAND_FLAG
    return _POSIX_COMMAND_FLAG


@dataclass(frozen=True)
class ShellConfig:
    """Pi parity ``getShellConfig()`` result: the shell AND how to invoke it.

    The two are inseparable once Windows is in scope. The spawn site used to
    hard-code ``-c``, which is correct for every POSIX shell and wrong for
    ``cmd.exe`` (``/c``) and unreliable for PowerShell (``-Command``), so the
    flag has to be resolved together with the path rather than assumed.
    """

    path: str
    command_flag: str = _POSIX_COMMAND_FLAG


def _resolve_shell(
    env: dict[str, str],
    shell_path: str | None = None,
    *,
    platform: str | None = None,
) -> ShellConfig:
    """Pi parity ``getShellConfig()`` resolution chain (``utils/shell.ts``).

    ``platform`` defaults to :data:`sys.platform` and exists so tests can drive
    the win32 arm from a POSIX box. It is injected rather than monkeypatched
    because ``shutil.which`` *itself* branches on ``sys.platform`` and then
    calls ``_winapi``, which is ``None`` off Windows — patching the global would
    crash the very PATH probe under test.

    Resolution order:

    1. An explicit ``shell_path`` (Pi parity ``getShellConfig(customShellPath)``
       — validated with :meth:`Path.exists`; raises ``ValueError`` with Pi's
       ``Custom shell path not found: {path}`` message when absent).
    2. ``$SHELL`` (Aelix-additive — documented ``$SHELL``-first divergence so
       user-configured shells win when the env exports one).
    3. ``/bin/bash`` → ``bash`` on ``PATH`` → ``/bin/sh`` (Pi's Unix chain).

    On Windows (#104) none of steps 2-3 can succeed: ``$SHELL`` is normally
    unset, ``/bin/bash`` does not exist, and the ``/bin/sh`` floor is not a
    program — so every bash call fell through to a guaranteed spawn failure.
    The win32 arm resolves the platform's real shells instead. It is EXPERIMENTAL
    (see ``SLICE-STATUS.md``): unlike the POSIX chain it has no live coverage.
    """

    if shell_path:
        if Path(shell_path).exists():
            return ShellConfig(shell_path, _command_flag_for(shell_path))
        raise ValueError(f"Custom shell path not found: {shell_path}")
    if (platform if platform is not None else sys.platform) == "win32":
        return _resolve_shell_win32(env)
    shell = env.get("SHELL")
    if shell:
        return ShellConfig(shell, _command_flag_for(shell))
    if Path("/bin/bash").exists():
        return ShellConfig("/bin/bash")
    bash_on_path = shutil.which("bash")
    if bash_on_path:
        return ShellConfig(bash_on_path)
    return ShellConfig("/bin/sh")


def _resolve_shell_win32(env: dict[str, str]) -> ShellConfig:
    """Windows shell chain (#104): ``$SHELL`` → PowerShell → ``%COMSPEC%``.

    ``$SHELL`` is honoured ONLY when it names a file that exists, because the
    common way it is set on Windows is Git-Bash exporting the MSYS path
    ``/usr/bin/bash`` — a path :class:`subprocess.Popen` cannot spawn. A user
    who exports a real ``bash.exe`` keeps bash (and with it the AUTO-mode
    classifier); everyone else gets PowerShell, which is the native shell but
    which the bash classifier cannot read — see
    :func:`~aelix_coding_agent.builtin.bash_classifier.is_classifiable_shell`.
    """

    shell = env.get("SHELL")
    if shell and Path(shell).exists():
        return ShellConfig(shell, _command_flag_for(shell))
    for exe in ("pwsh", "powershell"):
        found = shutil.which(exe, path=env.get("PATH"))
        if found:
            return ShellConfig(found, _POWERSHELL_COMMAND_FLAG)
    comspec = env.get("COMSPEC")
    if comspec:
        return ShellConfig(comspec, _command_flag_for(comspec))
    return ShellConfig("cmd.exe", _CMD_COMMAND_FLAG)


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
    """Pi parity ``BashOperations`` Protocol — swap surface for SSH/remote.

    ``on_data`` MUST NOT RAISE. All three in-repo callers pass
    ``chunks.append`` and cannot, but the contract is worth stating because it
    stopped being enforceable by the call stack in #222: the local
    implementation delivers from a loop callback, so an exception out of
    ``on_data`` reaches ``loop.call_exception_handler`` (a logged
    "Exception in callback") instead of the caller's ``await``.
    """

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
    """Local default — ``subprocess.Popen`` w/ a :class:`ProcessTree` kill on abort.

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
            # The cast is about the CHECKER, and it is ``run_contained``'s:
            # unpacking a ``dict[str, Any]`` costs pyright the text/bytes
            # overload discrimination and it settles on ``Popen[str]``. At
            # runtime ``PIPE`` with no ``text``/``encoding`` is bytes.
            proc = cast(
                "subprocess.Popen[bytes]",
                subprocess.Popen(  # noqa: S603
                    [shell.path, shell.command_flag, command],
                    cwd=cwd,
                    env=env_dict,
                    # #222 §A.5, and Pi's ``stdio: ["ignore", "pipe", "pipe"]``.
                    # The child used to inherit Aelix's stdin, where under
                    # ``start_new_session=True`` it does NOT stop on SIGTTIN —
                    # it has no controlling terminal, so the kernel's
                    # background-group test never applies. What happened
                    # instead was invisible: measured, a ``!cat`` typed into
                    # the TUI took the user's keystroke (the child got
                    # ``'secret\n'``, the TUI's own ``read`` got ``b''``) and
                    # never returned, and a child that called ``tcsetattr``
                    # left ECHO off after it exited. On POSIX ``DEVNULL`` +
                    # ``setsid`` makes the child non-interactive (``cat``
                    # prints nothing and exits 0; ``git commit`` without
                    # ``-m`` fails with "Aborting commit due to empty commit
                    # message." — the string measured 2026-09-06 from git
                    # itself, not the paraphrase this comment used to quote).
                    # On win32
                    # ``containment_spawn_kwargs`` returns only
                    # ``CREATE_NEW_PROCESS_GROUP``, so the child keeps Aelix's
                    # console: a real stdin reader gets EOF from ``NUL``, but a
                    # program that reads ``CONIN$`` directly (git credential
                    # prompts) or ``Read-Host`` still prompts there and still
                    # costs the whole timeout — and only when
                    # ``default_timeout != 0`` (see :data:`_DEFAULT_TIMEOUT`).
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # ``new_session=True`` is today's ``start_new_session=True``
                    # spelled through the primitive, so every tool child still
                    # makes its own session on POSIX and gains a process group
                    # (and, below, a job) on win32.
                    **containment_spawn_kwargs(new_session=True),
                ),
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            on_data(f"[bash] failed to spawn: {exc}\n".encode())
            return ExecExitResult(exit_code=127)

        loop = asyncio.get_running_loop()
        # The reader thread's two channels into this coroutine: ``eof`` is what
        # the drain waits on, ``state`` is the clock the post-kill idle rule is
        # measured from (``last_chunk_at`` stamped by the reader,
        # ``exited_at`` by whichever leg kills first).
        eof = asyncio.Event()
        state = _ReadState(last_chunk_at=time.monotonic())
        # Read on the LOOP thread by ``_deliver`` and cleared there too, in the
        # teardown below: it is what makes "no ``on_data`` after ``exec``
        # returns" true of the SITE. :meth:`_PipeReader.detach` cannot promise
        # it on its own — one delivery already past its ``_detached`` test can
        # still be in flight — and the callers join ``chunks`` with no await
        # (``create_bash_tool``'s ``execute``, ``cli/repl.py``, ``rpc_mode``).
        delivering = True

        def _post(callback: Callable[..., None], *args: Any) -> None:
            """Run ``callback`` on the loop. Called from the reader thread.

            ``call_soon_threadsafe`` raises ``RuntimeError`` on a CLOSED loop,
            which :meth:`_PipeReader.run`'s ``except (OSError, ValueError)``
            does not catch — measured in the #222 critique as a
            ``threading.excepthook`` traceback printed out of a TUI. The
            repo's other daemon→loop pump suppresses the same
            (``rpc_mode.py``'s print pump), and a detached reader outliving
            its call is exactly the shape that reaches a closed loop.
            """

            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(callback, *args)

        def _deliver(chunk: bytes) -> None:
            """Hand one chunk to ``on_data``, on the loop, while still delivering.

            AN ``on_data`` THAT RAISES NO LONGER REACHES THE CALLER, and that is
            a real change of shape from ``main``'s ``to_thread`` read, where the
            exception came out of the ``await`` (#222 critique POSIX-3).
            Delivery is a loop CALLBACK now, so a raise ends in
            ``loop.call_exception_handler`` — logged, not propagated — and
            ``exec`` returns normally with the rest of the output. Unreachable
            in-repo: all three callers pass ``chunks.append``
            (``create_bash_tool``'s ``execute``, ``cli/repl.py``,
            ``rpc_mode.py``), which is why this is stated rather than belted;
            :class:`BashOperations` carries the contract for a caller outside
            the repo.
            """

            if delivering:
                on_data(chunk)

        try:
            # BEFORE any await, so on win32 the job is assigned while the
            # window in which a descendant can escape it is one statement wide
            # (ADR-0238 §B.4). ``kill_on_close=False``: a command that exits 0
            # after backgrounding a daemon keeps it (Pi #8225).
            tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))
            assert proc.stdout is not None  # ``stdout=PIPE`` above
            # NOT ``asyncio.to_thread(proc.stdout.read, …)`` any more (#222
            # §A.2-3). After a kill whose pipe is held by a process OUTSIDE the
            # tree — a ``setsid`` descendant on POSIX — that read can be
            # neither cancelled nor abandoned: the default executor's threads
            # are not daemons, and measured, an abandoned read held
            # ``asyncio.run`` for the escapee's whole 30 s, after which the
            # stdlib path is ``THREAD_JOIN_TIMEOUT = 300`` and then an
            # unbounded ``_python_exit`` join. ``_wait`` below keeps its
            # ``to_thread`` because it blocks on the ROOT only, which the
            # ``proc.kill()`` belt in ``_end_the_tree`` always ends.
            reader = _PipeReader(
                proc.stdout,
                state,
                on_chunk=lambda chunk: _post(_deliver, chunk),
                on_eof=lambda: _post(eof.set),
            )
            reader.start()
        except BaseException:
            # A spawned child must never be left running by an exception
            # between the spawn and the first guarded statement (#221 review
            # posix-runner-1). ``tree`` is exactly what may be unbound here, so
            # this cannot be ``_end_the_tree``; the root is what there is to
            # end, and on POSIX ``Popen.kill`` polls first, so it cannot reach
            # a recycled pid. No reap: this runs on the loop thread and the
            # zombie is collected by the stdlib's ``_active`` sweep at the next
            # spawn — a bounded ``proc.wait`` here would stall the loop for a
            # child that is already dead.
            with contextlib.suppress(OSError):
                proc.kill()
            raise

        def _mark_the_kill() -> None:
            """Stamp the kill instant, for whichever leg gets there first.

            :attr:`_ReadState.exited_at` is documented as exactly that instant
            and it is what arms the post-kill drain below; the three legs
            (timeout, abort watcher, ``CancelledError``) can overlap, and the
            first one's clock is the honest origin for the idle rule.

            CALLED AFTER THE LADDER at all three legs, never before it, exactly
            as ``run_contained`` stamps at the reap and not at the
            ``TimeoutExpired`` (``_process_tree.py``'s timeout leg,
            ``state.exited_at`` after :func:`_end_the_tree`). Stamping first
            would fold the kill's OWN cost into the 0.1 s idle window the drain
            then measures from here: inert on POSIX, where the ladder is 1.6 ms,
            but on win32 ``hard_kill`` shells out to ``taskkill.exe`` under a 5 s
            bound, so the window would already be spent when the first byte the
            kill released arrives and the idle rule would collapse to zero arms.
            """

            if state.exited_at is None:
                state.exited_at = time.monotonic()

        # Track whether the timeout (not the abort signal) triggered the kill,
        # so the bash tool can label the result correctly (issue #11).
        _timed_out = False

        async def _wait() -> int | None:
            nonlocal _timed_out
            try:
                return await asyncio.to_thread(proc.wait, timeout)
            except subprocess.TimeoutExpired:
                _timed_out = True
                # ONE call, not ``hard_kill()`` and then this: ``_end_the_tree``
                # BEGINS with ``hard_kill``, and two of them are two
                # ``taskkill.exe`` spawns on win32 (#222 critique WIN32-12).
                # Its ``proc.wait(reap)`` deliberately races the ``to_thread``
                # worker this coroutine is on; safe under ``_waitpid_lock``
                # (critique: 20/20 trials, both returned ``-9``).
                _end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)
                _mark_the_kill()
                return None

        # Track whether the abort signal (not timeout) triggered a kill so we
        # can return exit_code=None parity with the timeout-kill path.
        _signal_aborted = False

        async def _watch_signal() -> None:
            """End the tree when the abort signal fires.

            The stamp is after the ladder here too, and it still precedes the
            drain that reads it: from ``signal.wait()`` returning to the stamp
            there is no ``await``, so the whole leg runs to completion on the
            loop thread before ``_wait``'s ``to_thread`` callback can resume the
            coroutine below — including ``_end_the_tree``'s blocking
            ``proc.wait(reap)``, which holds the loop rather than yielding it.
            """
            nonlocal _signal_aborted
            assert signal is not None  # watcher only started when signal is set
            await signal.wait()
            _signal_aborted = True
            _end_the_tree(tree, proc, reap=INTERRUPT_REAP_SECONDS)
            _mark_the_kill()

        async def _drain_past_the_kill(exited_at: float) -> None:
            """``_drain``'s idle rule, awaited instead of polled.

            Same three ends as the synchronous one in
            ``aelix_ai/utils/_process_tree.py`` and the same two constants: EOF,
            or the pipe idle for :data:`EXIT_DRAIN_SECONDS` measured from
            ``max(last_chunk_at, exited_at)``, or the absolute cap one
            :data:`KILL_DRAIN_SECONDS` past the kill. A flat cap without the
            idle rule would cost Esc a flat 1.0 s where ``run_contained`` costs
            ~0.1 s (#221 review TP4/PI-2 measured that mistake), and
            ``not reader.is_alive()`` is the third end because a reader that
            ended through its ``except`` leg is done whatever ``eof`` says
            (#221 HC5). There is no poll: a 5 ms one costs 0.71 % of a core and
            ~163 loop wakeups/s for the holder's whole life.
            """

            cap = exited_at + KILL_DRAIN_SECONDS
            while not eof.is_set() and reader.is_alive():
                armed_at = max(state.last_chunk_at, exited_at)
                remaining = min(cap, armed_at + EXIT_DRAIN_SECONDS) - time.monotonic()
                if remaining <= 0:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(eof.wait(), remaining)
            # ONE turn of the loop before the caller detaches (#222 critique
            # POSIX-1/WIN32-1/ASYNC-1). The chunk callbacks and the EOF callback
            # share the loop's FIFO, so waiting on ``eof`` cannot resume before
            # every earlier chunk has been delivered — but a ``wait_for`` that
            # TIMED OUT resumes with callbacks still queued behind it. The
            # 15-in-250 loss that rule comes from was measured on the POLLED
            # shape; deleting this yield reddened nothing here in 40 rounds.
            await asyncio.sleep(0)

        async def _drain_to_the_end() -> None:
            exited_at = state.exited_at
            if exited_at is None:
                # The root exited on its own: wait for EOF, unbounded, exactly
                # as ``main``'s ``await drain_task`` did. A backgrounded helper
                # holding stdout therefore still holds this call open — a
                # product decision left out of #222 deliberately (§H) and
                # pinned by ``test_bash_tool_containment.py``'s exit-path case
                # so the follow-up's change is visible.
                await eof.wait()
                return
            await _drain_past_the_kill(exited_at)

        watcher_task: asyncio.Task[None] | None = None
        if signal is not None and hasattr(signal, "wait"):
            watcher_task = asyncio.create_task(_watch_signal())
        # FOUR ``finally``s, innermost first, and the ORDER is the contract:
        # watcher → drain → detach → close, which is ``main``'s order and is
        # restored here after the branch's first shape put the watcher two
        # levels out (#222 pre-merge review F1).
        #
        # THE WATCHER IS DISARMED FIRST because it must not be able to fire
        # DURING the drain. Measured with it armed there (abort at t=1.0 s into
        # an rc-0 command that had backgrounded a 20 s helper): ``main`` returns
        # ``exit_code=0`` with the helper alive, the branch returned
        # ``exit_code=None`` at 1.01 s with the helper killed and the output
        # after the abort lost — i.e. an abort landing in the post-exit drain
        # killed exactly the tree ``kill_on_close=False`` exists to keep, and on
        # win32 ``hard_kill`` is ``taskkill /T /F`` + ``TerminateJobObject``,
        # which reaches such a helper even through a process group of its own.
        # Whether an abort in that window SHOULD do anything is #230's to
        # decide; it is not this issue's to decide by accident.
        #
        # THE DETACH IS OUTSIDE THE DRAIN because the drain is the only
        # cancellable step: with the two in one block a single Esc landing
        # during the drain skipped the detach and re-opened #221 site-exec-1's
        # retention — 14.9 GB against 0 MB after 2 s of a chatty holder.
        #
        # ``close()`` IS OUTERMOST and is never skipped: a ``CancelledError``
        # raised in an inner ``finally`` propagates into the enclosing ``try``
        # (measured under 2x and 3x cancels). It also has to stay outside the
        # watcher, because ``hard_kill`` early-returns on a closed tree, so a
        # ``close()`` that beat the watcher would turn a firing abort into a
        # silent no-op.
        try:
            try:
                try:
                    try:
                        exit_code = await _wait()
                    except asyncio.CancelledError:
                        # Esc-path: the harness cancelled our turn task. End the
                        # tree so it does not become an orphan, then re-raise
                        # after the drain so the caller sees the cancellation.
                        _end_the_tree(tree, proc, reap=INTERRUPT_REAP_SECONDS)
                        _mark_the_kill()
                        raise
                    finally:
                        if watcher_task is not None:
                            watcher_task.cancel()
                            # Swallow the watcher's own cancellation (and any
                            # teardown error) — the outer turn cancellation is
                            # captured and re-raised at the ``except
                            # asyncio.CancelledError`` above.
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await watcher_task
                finally:
                    await _drain_to_the_end()
            finally:
                reader.detach()
                delivering = False
        finally:
            # A release, never a kill: ``kill_on_close=False``. POSIX signals
            # nothing here; win32 closes the job handle.
            tree.close()
        # Pi parity: signal-kill and timeout-kill both report exit_code=None.
        if _signal_aborted:
            exit_code = None
        # Issue #11: a signal-abort takes precedence over a timeout label (if
        # both somehow fired, the user's abort is the operative cause).
        return ExecExitResult(
            exit_code=exit_code, timed_out=_timed_out and not _signal_aborted
        )


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
        # Pi parity, verbatim: ``bashToolSystemPromptContribution.snippet``
        # (``coding-agent/src/core/tools/bash.ts:46-49``).
        prompt_snippet="Execute bash commands (ls, grep, find, etc.)",
        # NOT pi's bash guideline. Pi's reads "You can inspect PI_*
        # environment variables for current model and session details" —
        # aelix has no ``PI_*`` and porting it would hand the model a variable
        # namespace that does not exist, which is the class of overclaim #120
        # exists to remove. These two are aelix's own, lifted out of the static
        # Guidelines list where a ``--no-tools`` run still advertised bash.
        prompt_guidelines=(
            "After editing, verify your work (run the relevant tests or build "
            "via bash when appropriate).",
            "Be careful with destructive or irreversible shell commands; do "
            "not run them unless the intent is clear.",
        ),
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
