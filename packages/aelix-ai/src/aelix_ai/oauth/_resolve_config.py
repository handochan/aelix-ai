"""Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).

Stored configuration values in Pi's ``auth.json`` can use two
indirection forms:

- ``!<command>``: the rest of the string is executed as a shell
  command — ``sh -c <command>`` on POSIX, with the ``sh`` resolved to an
  absolute path on ``PATH`` since #241, and on win32 the first shell
  of the resolved chain that spawns (#227); the trimmed stdout becomes
  the resolved value. Per-command results are cached so repeated reads
  do not re-fork the shell; the ``models.json`` family below takes a
  SECOND, opt-in cache with its own key space (#240). Pi runs it under
  a POSIX shell if one is
  there and the native shell otherwise, which is the same shape; the
  divergence is that Aelix reaches PowerShell before ``cmd.exe`` (its
  own #104 order) and hardens both (ADR-0235 asks for no ADR, but a
  parity-pinned header must not imply parity it no longer has).
- ``<env-name>``: when the literal string matches an environment
  variable name, its value is substituted in. If the env var is unset,
  the literal value is returned verbatim (Pi behavior).

This module ports the helper into Aelix so stored ``api_key`` entries
honor the Pi convention (Sprint 6e W6, P-141). Without it, a Pi-style
``auth.json`` entry like ``"key": "OPENAI_API_KEY"`` would have leaked
the env-var NAME as the API key.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from aelix_ai.utils._child_output import decode_child_output
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _resolve_platform,
    _retained_handle,
    containment_spawn_kwargs,
)
from aelix_ai.utils._shell import (
    CMD_NAMES,
    NOT_A_RUNNABLE_SHELL,
    POWERSHELL_NAMES,
    ShellConfig,
    _env_get,
    _which_on_path,
    shell_basename,
    windows_command_shells,
)

# Pi's ``execSync`` enforces an implicit ~1 MB ``maxBuffer`` (throws
# ``ENOBUFS`` on overflow) and a 10 s timeout. Python's ``subprocess`` has
# no ``maxBuffer`` equivalent, so a runaway ``!command`` (``!yes`` /
# ``!cat /dev/urandom``) would buffer unbounded and OOM/hang the host. The
# bounded reader below restores that guard (ADR-0140 review hardening).
_MAX_OUTPUT_BYTES = 1024 * 1024
_COMMAND_TIMEOUT = 10.0


#: One poll quantum. The stop is reported by the first ``waitpid`` after the
#: kernel delivers the signal, so this IS the detection latency: measured
#: 0.054 s on darwin and 0.052-0.059 s on Linux/dash (py3.11 and py3.12, root
#: and non-root), against the 10 s of ``_COMMAND_TIMEOUT`` it replaces. The
#: probe itself costs 0.20 us per call.
_STOP_POLL_SECONDS = 0.05

#: The two stops that mean "this helper tried to talk to the terminal", mapped
#: to what it was doing. Neither signal exists on win32, where there is no
#: background process group to be stopped for — the mapping is then EMPTY and
#: the detector inert, which is what makes a non-terminal stop (``SIGSTOP``,
#: ``SIGTSTP``) keep today's unnamed timeout on every platform.
_TERMINAL_STOP_SIGNALS: dict[int, tuple[str, str]] = {
    int(sig): (name, what)
    for name, what in (
        ("SIGTTIN", "reading the terminal"),
        ("SIGTTOU", "changing the terminal's settings"),
    )
    if (sig := getattr(signal, name, None)) is not None
}

#: ``WUNTRACED`` is what makes ``waitpid`` report a STOP rather than only an
#: exit. It does not exist on win32; zero disables the probe there.
_WAIT_STOP_OPTIONS = (
    (os.WNOHANG | os.WUNTRACED)  # pyright: ignore[reportAttributeAccessIssue]
    if hasattr(os, "WUNTRACED")
    else 0
)


@dataclass(slots=True)
class _Failure:
    """A named cause's seat on the way back out through three ``None`` returns.

    ``_run_shell_command`` reports failure as :data:`None`, and so do both
    uncached hops above it, so there is nowhere in the return values for a
    reason to ride. Callers that can render one pass this in; callers that
    cannot pass nothing and keep today's messages exactly.
    """

    reason: str | None = None
    #: Filled on the stop branch ONLY, where it is the ``-9`` of the
    #: ``SIGKILL`` :meth:`ProcessTree.hard_kill` actually sent. A timeout or an
    #: overflow keeps today's ``-1``, which renders as ``SIGHUP`` and would
    #: otherwise name a second signal next to the one in the reason.
    returncode: int | None = None
    #: The argv that actually SPAWNED, so a rendered failure names the shell
    #: that ran instead of the ``sh`` this site used to assume (#227). Set to
    #: the first candidate before the loop, so a chain where nothing spawns
    #: still names one. A ``str`` is the raw command line the ``cmd`` family
    #: needs; see :func:`_shell_argv`.
    argv: list[str] | str | None = None


class _StoppedByTerminal(subprocess.CalledProcessError):
    """A ``CalledProcessError`` that also says the command was STOPPED, and why.

    The base class is load-bearing for its ``__str__``, not for any catcher.
    ``CalledProcessError.__str__`` renders the ``Command '[...]' died with
    <Signals.SIGKILL: 9>.`` half of the message that
    ``test_a_helper_that_reads_the_terminal_fails_fast_and_names_the_cause``
    asserts; rebased on ``subprocess.SubprocessError`` that case goes red
    (measured: ``1 failed, 4 passed``) and the message degrades to a bare
    ``(-9, [...])``. Nothing is preserved by NAME: no
    ``except subprocess.CalledProcessError`` exists anywhere in ``packages/``,
    and the cascade's own catcher is the ``except Exception`` in
    ``ModelRegistry.get_api_key_and_headers``, which catches this either way.
    Deliberately not exported: callers gain a longer message, not a new type
    to handle.
    """

    def __init__(
        self, returncode: int, cmd: list[str] | str, reason: str
    ) -> None:
        super().__init__(returncode, cmd)
        self.reason = reason

    def __str__(self) -> str:
        return f"{super().__str__()} {self.reason}"


def _stopped_by_the_terminal(
    proc: subprocess.Popen[bytes], *, platform: str | None = None
) -> tuple[str, str] | None:
    """The (signal name, what it was doing) of the stop, or :data:`None`.

    POSIX only, and it says so rather than guessing: on win32 there is no
    ``WUNTRACED`` and no background process group, so the probe is not even
    attempted and every ``!command`` keeps the behaviour it has today.

    THE PROBE PAYS FOR ITSELF TWICE, and both are deliberate. (1) Calling
    ``waitpid`` on a child that has ALREADY exited takes the status ``Popen``
    was going to reap, and ``Popen._try_wait`` swallows the resulting
    ``ChildProcessError`` as ``sts = 0`` — measured: an ``exit 7`` read back as
    ``exit 0``, a failed helper reported as a successful one. So the status is
    handed back through ``os.waitstatus_to_exitcode``. (2) The same call
    consumes the zombie, which is what pinned the pgid; see the amended
    PID/PGID paragraph in :mod:`aelix_ai.utils._process_tree`.
    ``os.waitid(..., WNOWAIT)`` avoids both and is absent on darwin; forking on
    platform was refused because the gating POSIX leg is Linux only, so the
    darwin repair path would then never run in CI.
    """

    if _resolve_platform(platform) == "win32" or _WAIT_STOP_OPTIONS == 0:
        return None
    if proc.returncode is not None:  # already reaped — nothing left to poll
        return None
    try:
        pid, status = os.waitpid(proc.pid, _WAIT_STOP_OPTIONS)
    except (ChildProcessError, OSError):
        return None
    if pid == 0:
        return None
    if os.WIFSTOPPED(status):  # pyright: ignore[reportAttributeAccessIssue]
        return _TERMINAL_STOP_SIGNALS.get(
            os.WSTOPSIG(status)  # pyright: ignore[reportAttributeAccessIssue]
        )
    if proc.returncode is None:
        proc.returncode = os.waitstatus_to_exitcode(status)
    return None


#: What a ``!command``'s PowerShell is started with, and why each half is here.
#: ``-NoProfile`` is correctness: measured on PowerShell 7, a profile that
#: writes to stdout is PREPENDED to the resolved key (``profile-banner\nsk-KEY``).
#: ``-NonInteractive`` is NOT about timing — ``stdin=DEVNULL`` (this site's
#: shape since ADR-0140; the bash tool's since #222) already ends a plain
#: ``Read-Host``, measured 0.652 s — it is about the three things
#: stdin cannot reach: the prompt TEXT landing inside the key (measured,
#: ``'give me a key: \nGOT:'`` against ``'GOT:'``), the masked read
#: (``Read-Host -AsSecureString``, ``Get-Credential``) which opens ``CONIN$``
#: with ``CreateFile`` and so costs the whole ``_COMMAND_TIMEOUT``, and the
#: prompt WRITE to ``CONOUT$`` that redirection cannot capture. The last two are
#: read from PowerShell 7.6.5's ConsoleHost sources, not measured, and Windows
#: PowerShell 5.1 — what a stock box actually lands on — is a different
#: implementation nobody has run this against.
_POWERSHELL_HARDENING = ("-NoProfile", "-NonInteractive")

#: ``/d`` is ``-NoProfile``'s counterpart for ``cmd``'s registry ``AutoRun``;
#: ``/s`` makes its quote stripping deterministic. Both are reasoned from
#: ``cmd /?``'s documented switches, NOT measured on win32.
_CMD_HARDENING = ("/d", "/s")

#: ``subprocess.CREATE_NO_WINDOW``, spelled as a literal for the same reason
#: ``CREATE_NEW_PROCESS_GROUP`` is one: the name does not exist off Windows
#: (measured: ABSENT on darwin) and the win32 arm of these tests is read there.
CREATE_NO_WINDOW = 0x0800_0000


def _shell_argv(shell: ShellConfig, cmd: str) -> list[str] | str:
    """How THIS caller invokes one resolved shell.

    The family is the PRIMITIVE's answer; the hardening is this caller's policy.
    :mod:`aelix_ai.utils._shell` owns which shell a machine has, and
    ``tools/bash.py`` — which runs the user's own interactive shell — must keep
    their PowerShell profile where a ``!command`` must not, so the two flag
    tuples above stay here.

    The family is read with ``shell_basename`` against ``POWERSHELL_NAMES`` /
    ``CMD_NAMES``, the same three names ``dialect_for_shell`` imports, rather
    than a second table. A literal set here would be that second copy and the
    drift is measured: ``{"pwsh", "powershell"}`` against ``Path(p).stem.lower()``
    diverges on 18 of 66 candidate paths — a ``$SHELL`` of ``pwsh-7.5.0.exe``
    would take ``-Command`` with NO ``-NoProfile`` (the profile banner inside the
    key) and ``command.com`` would take ``/d`` beside a POSIX ``-c``. Dispatching
    on ``shell.command_flag`` agrees on every candidate the primitive produces
    today, but rests on an invariant :class:`ShellConfig` does not enforce.

    The ``cmd`` family returns a RAW COMMAND LINE. ``cmd /?``'s rule 2 strips one
    leading and one trailing quote and ``cmd`` implements no ``\\"`` at all, so
    :func:`subprocess.list2cmdline`'s rendering would hand the child literal
    backslash-quotes; the PowerShell family keeps the list because on win32 the
    .NET host CRT-parses the command line back into argv first, where ``\\"`` IS
    the documented escape. This branch is win32-only — POSIX's "a ``str`` is the
    program name" is never reached — and CPython's win32 ``_execute_child``
    passes a ``str`` to ``CreateProcess`` verbatim. The shell path is QUOTED: a
    spaced ``%COMSPEC%`` or ``$SHELL`` is what a bare f-string breaks.

    ``command.com`` is NOT covered by the ``cmd`` row: ``shell_basename`` strips
    only ``.exe``, so it takes the POSIX branch with the ``-c`` it cannot use.
    That is pre-existing (#104), neither introduced nor fixed here.
    """

    name = shell_basename(shell.path)
    if name in POWERSHELL_NAMES:
        return [shell.path, *_POWERSHELL_HARDENING, shell.command_flag, cmd]
    if name in CMD_NAMES:
        hardening = " ".join(_CMD_HARDENING)
        return f'"{shell.path}" {hardening} {shell.command_flag} "{cmd}"'
    return [shell.path, shell.command_flag, cmd]


def _shell_argv_candidates(
    cmd: str, *, platform: str | None = None, env: Mapping[str, str] | None = None
) -> list[list[str] | str]:
    """Every shell this machine might run ``cmd`` under, best first.

    POSIX is one candidate, as it has always been — but since #241 it is an
    ABSOLUTE ``sh`` rather than the bare name. ``["sh", "-c", cmd]`` goes to
    ``execvp``, which searches an empty or relative ``PATH`` component against
    the current directory: measured on darwin, ``PATH=":/usr/bin:/bin"`` with a
    planted ``sh`` in the cwd ran the planted file on the credential path. The
    price is one ``PATH`` walk per spawn (24.4 µs on a 25-entry miss). The list
    is longer than one only on win32, where ``sh`` is not a given:

    ===  ================  ==========================================
    \\#    candidate         invocation
    ===  ================  ==========================================
    1    ``$SHELL``        ``[path, flag, cmd]`` — only when it names an
                           existing file, as the bash tool honours it
    2    ``sh`` on PATH    ``[path, "-c", cmd]`` — keeps today's behaviour on
                           a Windows box that has one and no ``$SHELL`` set
                           (Git-for-Windows, MSYS2, Cygwin); step 1 still wins
    3    ``pwsh``          ``[path, *_POWERSHELL_HARDENING, "-Command", cmd]``
    4    ``powershell``    same
    5    ``%COMSPEC%``     a raw command line, ``"<path>" /d /s /c "<cmd>"`` —
                           only when it is absolute (#241)
    6    ``%SystemRoot%``  same, against ``<root>\\System32\\cmd.exe`` when that
                           file exists; ``C:\\Windows`` when the key is unset
    7    ``cmd.exe``       same — the floor, and the only candidate that names
                           no path at all
    ===  ================  ==========================================

    Steps 1 and 3-7 are ``_resolve_shell_win32``'s chain (#104, with step 6 added
    at the floor by #241), so one machine gets one shell answer for both callers;
    step 2 is the only difference and is deliberately not taken by the bash tool
    (ADR-0237/#204).

    ``platform`` and ``env`` are resolution seams, spelled like
    :func:`_stopped_by_the_terminal`'s and read through the same
    :func:`_resolve_platform`. ``env`` is a resolution seam ONLY — it is never
    passed to :class:`subprocess.Popen`, so the child still inherits
    :data:`os.environ`. Its default is resolved ABOVE the platform branch
    because both production callers pass no ``env`` at all and the POSIX arm now
    reads it too; leaving the default inside the win32 branch would raise
    ``AttributeError`` on every ``!command``. ``windows=False`` at that arm is a
    fact rather than a seam value — the win32 branch returns below it.
    """

    env = os.environ if env is None else env
    if _resolve_platform(platform) != "win32":
        sh = (
            _which_on_path(
                "sh", path=_env_get(env, "PATH", fold=False), env=env, windows=False
            )
            or "/bin/sh"
        )
        return [[sh, "-c", cmd]]
    return [
        _shell_argv(shell, cmd)
        for shell in windows_command_shells(env, include_posix_sh=True)
    ]


def _spawn_kwargs(platform: str | None = None) -> dict[str, Any]:
    """The containment kwargs, plus this site's own win32 console flag.

    ``CREATE_NO_WINDOW`` is OR'd in HERE and not inside
    :func:`containment_spawn_kwargs`. Pi passes ``windowsHide: true`` at exactly
    this spawn and Node's default is false; CPython gives it for free only for
    ``shell=True`` (its single ``STARTF_USESHOWWINDOW``/``SW_HIDE`` assignment
    sits inside ``_execute_child``'s ``if shell:`` branch) and this site spawns a
    list argv. Before #227 the win32 spawn of ``sh`` failed before an image
    loaded, so the question never arose; now a console-subsystem shell really is
    spawned, and ``CreateProcess`` gives a console child a NEW console when the
    parent has none — a ``pythonw``-shaped host is a shape this repo already
    defends against.

    It must not move into the shared helper: three of that helper's other six
    call sites (``extensions/subprocess_hooks``, ``rpc/rpc_client``, and
    ``aelix_agents/print_channel``, whose tree the reaper soft-kills) end their
    trees with ``ProcessTree.soft_kill()`` -> ``ctrl_break()``, which needs a
    shared console, so the flag there would silently demote three teardowns to
    hard kills. The other three (``tools/bash.py``, ``tools/_subprocess.py``,
    ``_process_tree.run_contained``) hard-kill only, as this site's ladder does.
    The price is that a win32 console prompt becomes certainly unanswerable,
    which is what :func:`_run_shell_command`'s #226 clause already says.
    **What Windows does with the flag is reasoned, not measured.**
    """

    kwargs = containment_spawn_kwargs(platform=platform)
    if _resolve_platform(platform) == "win32":
        kwargs["creationflags"] |= CREATE_NO_WINDOW
    return kwargs


def _run_shell_command(
    cmd: str,
    *,
    failure: _Failure | None = None,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str] | None:
    """Run ``cmd`` under a resolved shell, with a timeout AND a ~1 MB cap.

    Mirrors Pi's ``execSync`` (``timeout: 10000`` + the implicit ~1 MB
    ``maxBuffer`` that throws ``ENOBUFS`` on overflow). Returns
    ``(returncode, stdout_text)`` or :data:`None` on a spawn error,
    timeout, or output-cap overflow — so a runaway producer can no longer
    OOM/hang the host. ``stderr`` is discarded (only stdout is consumed).

    THE SHELL IS RESOLVED, NOT ASSUMED (#227). This used to spawn ``sh -c`` on
    every platform, so on a stock Windows box — which has no ``sh`` — the spawn
    raised ``FileNotFoundError`` in about a millisecond and the value resolved
    to nothing, reported as ``Failed to resolve … from shell command:``, which
    blames the user's command for a shell that was never there. The single
    ``Popen`` is a loop over :func:`_shell_argv_candidates` now: POSIX still has
    exactly one candidate and spawns byte-identically, while win32 falls through
    to the next candidate when this one is missing, not executable, or **not a
    loadable program image** — classified by ERRNO, never by exception class,
    for the reason :data:`aelix_ai.utils._shell.NOT_A_RUNNABLE_SHELL` gives
    (spelled fully qualified: #243 moved that set down to the primitive both
    spawn sites now share, so an unqualified role would dangle here). The
    CONSTRUCTOR is what raises, so no command ran (POSIX reaps the failed fork inside
    ``Popen.__init__``; win32's ``CreateProcess`` fails atomically) and falling
    through cannot double-run anything. A malformed argv (``ValueError``) and
    every other spawn error keep today's ``None`` after one spawn, and a chain
    where every candidate fails still answers ``None`` — today's answer, not a
    new one. Everything after the spawn is untouched.

    CONTAINMENT (#202, ADR-0238). ``proc.kill()`` reached the shell and nothing
    else: measured on ``main`` 39549b9, ``sh -c "a | b"`` keeps every stage of
    the pipeline in the shell's group, so a timed-out ``!command`` left them
    running (``sh -c "sleep 5"`` hid it on darwin, whose ``/bin/sh`` is bash 3.2
    and ``exec``s a lone simple command, so killing the shell IS killing it;
    dash — the gating leg's ``/bin/sh`` — forks instead and never hid anything),
    and on Windows an MSYS ``sh.exe`` is an exec stub
    whose death orphans the command outright. The spawn now asks for a tree of
    its own and both kill sites end the tree
    (:mod:`aelix_ai.utils._process_tree`).

    The kwarg is ``process_group=0`` and NOT ``start_new_session=True``, for a
    reason narrower than this docstring first claimed (#226). A
    ``process_group=0`` child can OPEN ``/dev/tty`` but cannot READ it: its group
    is never the terminal's foreground group, so the kernel STOPS it —
    ``SIGTTIN`` on a read, ``SIGTTOU`` on a ``tcsetattr`` — unless it blocks or
    ignores ``SIGTTOU``, which POSIX permits and which was measured to succeed.
    What the kwarg buys is that the failure is an OBSERVABLE STOP on a terminal
    the child still has as its CONTROLLING terminal. The alternatives are both
    worse and both measured: a ``setsid`` helper that opens the terminal by path
    (``$GPG_TTY``, ``/dev/ttysNNN``) faces no job-control check at all and was
    measured eating the line the user had typed at Aelix's own prompt — silent
    theft in place of a visible stop — while passing no kwarg at all lets the
    group-delivered signal stop Aelix TOO when it is in the background (measured:
    both still stopped at 20 s, ``SIGCONT`` does not recover it, and the timeout
    below never fires because nothing is left running to fire it). ``killpg``
    reaches all three shapes identically.

    THE STOP IS DETECTED AND NAMED (#226). The wait below polls at
    ``_STOP_POLL_SECONDS`` and asks :func:`_stopped_by_the_terminal` each time,
    so a helper that tries to prompt fails in about one quantum — measured
    0.054 s under a real pty on macOS and 0.052-0.059 s on Linux/dash, the only
    POSIX CI leg — with a cause the caller can render, instead of burning the
    whole ``_COMMAND_TIMEOUT`` and saying nothing about the terminal. Both
    signals are delivered to the GROUP, so a pipeline's leader stops with the
    stage that read the terminal and this ``waitpid`` sees it; that matters most
    on the gating leg, where dash forks rather than ``exec``ing, so the helper is
    always the leader's child there. On win32 the detector is inert (there is
    no ``WUNTRACED`` and no background process group) and a console reader can
    still prompt — unanswered, for the full timeout. Nobody has watched that.
    #227 narrows that last sentence for PowerShell's OWN prompts only: the
    PowerShell candidates run ``-NonInteractive``, so a ``Read-Host`` is refused
    rather than left prompting and its prompt text can no longer land inside the
    key. A helper that opens the console itself (git, ssh, gpg) is untouched,
    and Windows PowerShell 5.1 — what a stock box actually resolves — is
    unmeasured.
    """

    candidates = _shell_argv_candidates(cmd, platform=platform, env=env)
    if failure is not None:
        # Before the loop, so a chain where NOTHING spawns still names a shell.
        failure.argv = candidates[0]
    proc: subprocess.Popen[bytes] | None = None
    for argv in candidates:
        try:
            # The cast is about the CHECKER. ``_spawn_kwargs()`` is a
            # ``dict[str, Any]``, and unpacking one costs pyright its ability to
            # discriminate ``Popen``'s text/bytes overloads: it settles on
            # ``Popen[str]`` (measured — ``text=False`` does not steer it back),
            # and the byte-counting reader below would then read as a type error
            # instead of as the output cap it is. At runtime ``stdout=PIPE`` with
            # no ``text``/``encoding`` is bytes, which is what the cast asserts.
            #
            # ``_spawn_kwargs()`` takes NO argument even when ``platform`` was
            # injected: an injected platform must never reach a real POSIX spawn,
            # where ``Popen(creationflags=…)`` raises ``ValueError`` (measured).
            proc = cast(
                "subprocess.Popen[bytes]",
                subprocess.Popen(  # noqa: S602 — intentional Pi-parity shell exec.
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    **_spawn_kwargs(),
                ),
            )
        except ValueError:
            return None  # a malformed argv is not a verdict on the shell
        except OSError as exc:
            if exc.errno in NOT_A_RUNNABLE_SHELL:
                continue  # not a runnable shell — try the next candidate
            return None  # today's answer for every other spawn error
        if failure is not None:
            failure.argv = argv
        break
    if proc is None:
        return None  # nothing in the chain spawned — today's answer

    # Attached before anything is read, because on win32 only descendants
    # created AFTER the job assignment inherit membership. ``kill_on_close``
    # stays False: a ``!command`` that deliberately backgrounds a helper keeps
    # it when the command itself succeeds, as it does today on every platform.
    tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))
    try:
        chunks: list[bytes] = []
        overflow = False

        def _read() -> None:
            nonlocal overflow
            total = 0
            stream = proc.stdout
            if stream is None:
                return
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_OUTPUT_BYTES:
                    overflow = True
                    break
                chunks.append(chunk)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()

        # Not ``reader.join(_COMMAND_TIMEOUT)``: the join is broken into poll
        # quanta so a stop can be SEEN. ``Thread.join`` still returns the moment
        # the reader finishes, so the happy path pays nothing for this (measured:
        # 3.13/3.56 ms before, 3.03/3.60 ms after, n=200).
        deadline = time.monotonic() + _COMMAND_TIMEOUT
        stopped: tuple[str, str] | None = None
        while True:
            reader.join(_STOP_POLL_SECONDS)
            if not reader.is_alive() or overflow or time.monotonic() >= deadline:
                break
            stopped = _stopped_by_the_terminal(proc)
            if stopped is not None:
                break

        if stopped is not None or reader.is_alive() or overflow:
            # Stopped by the terminal, timed out, or over the output cap — kill
            # and fail. Straight to the hard kill: there is no grace stage here
            # to escalate from, and the producer we are killing is by definition
            # not answering (a STOPPED one cannot answer at all).
            if stopped is not None and failure is not None:
                name, what = stopped
                failure.reason = (
                    f"The command stopped {what} ({name}): a !command runs in a "
                    "process group of its own, which is never the terminal's "
                    "foreground group, so the kernel stops it the moment it "
                    "touches the terminal — it cannot prompt you. Use a helper "
                    "that needs no terminal: an askpass program, a GUI pinentry, "
                    "or the OS keychain."
                )
            tree.hard_kill()
            # BOUNDED, because ``hard_kill`` is best-effort on win32: with no
            # job (a failed attach) and no resolvable ``taskkill.exe`` it is a
            # no-op that does not raise, and the bare ``proc.wait()`` this
            # replaced then blocked forever on a command that is by definition
            # hung — synchronously, in whatever thread resolved the config
            # (review win-leg/F3). Dropping the ``TerminateProcess`` leg is what
            # made that reachable; a kill that could not kill must cost a
            # timeout, not the thread. ``TimeoutExpired`` is a
            # ``SubprocessError``, not an ``OSError``.
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5.0)
            reader.join(1.0)
            if stopped is not None and failure is not None:
                # Read AFTER the kill, because it is the kill's own ``-9``. The
                # timeout and overflow branches keep today's ``-1``.
                failure.returncode = proc.returncode
            return None

        try:
            # stdout hit EOF, so the child has (almost) finished; bound the
            # reap so a process that closes stdout but lingers can't hang us.
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            tree.hard_kill()
            # Bounded for the same reason as the reap above (win-leg/F3): a
            # win32 ``hard_kill`` with no job and no ``taskkill.exe`` is a
            # silent no-op, and this path already returns ``None``.
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5.0)
            return None

        # #239: the DECODER, but no UTF-8 preamble — see ``_shell_argv``. The
        # argv stays #227's byte for byte because this stdout IS the credential.
        return proc.returncode, decode_child_output(b"".join(chunks))
    finally:
        # Release, not a kill: POSIX signals nothing here and win32 only closes
        # the job handle, which ends nothing without ``kill_on_close``.
        tree.close()


def resolve_config_value(
    value: str, cache: dict[str, str] | None = None
) -> str:
    """Pi parity: ``coding-agent/core/resolve-config-value.ts``.

    - When ``value`` starts with ``!``, treat the rest as a shell
      command and return the trimmed stdout. Results are cached per
      ``cache`` mapping (if provided) so repeated reads do not re-fork.
    - When ``value`` matches an environment variable name, return the
      env value.
    - Otherwise return ``value`` verbatim.

    The ``cache`` parameter is intentionally exposed so callers (e.g.
    :class:`AuthStorage`) can scope the cache to a single instance and
    invalidate it on demand. Pi caches per-process; Aelix scopes it
    tighter for testability.
    """

    if value.startswith("!"):
        cmd = value[1:]
        if cache is not None and cmd in cache:
            return cache[cmd]
        failure = _Failure()
        result = _run_shell_command(cmd, failure=failure)
        if result is None or result[0] != 0:
            # Preserve the raise-on-failure contract the auth.json cascade
            # relied on (was ``check=True``); a timeout or output-cap
            # overflow now fails here instead of hanging / OOMing.
            argv = failure.argv or ["sh", "-c", cmd]
            if failure.reason is not None:
                # The stop branch, and the only one that changes the returncode:
                # ``-9`` is the ``SIGKILL`` that ended it, where today's ``-1``
                # would render as ``SIGHUP`` and name a second signal beside the
                # one the reason already names.
                raise _StoppedByTerminal(
                    failure.returncode if failure.returncode is not None else -1,
                    argv,
                    failure.reason,
                )
            raise subprocess.CalledProcessError(
                result[0] if result is not None else -1, argv
            )
        # ``.strip()`` and not ``rstrip("\n")`` — Pi's ``.trim()``, and what
        # :func:`_execute_command_uncached` already did. A CROSS-PLATFORM change:
        # it also removes a leading newline and surrounding spaces and tabs the
        # old spelling kept. Under a shell whose ``echo`` emits CRLF — cmd.exe
        # and PowerShell both do — the old spelling left a bare carriage return
        # inside an ``Authorization`` header (measured: cached ``'sk-abc\r'``
        # against uncached ``'sk-abc'``).
        out = result[1].strip()
        if cache is not None:
            cache[cmd] = out
        return out
    return os.environ.get(value, value)


# ── models.json request-time resolution (P0 #4 / ADR-0140) ────────────────
#
# Pi parity: ``resolve-config-value.ts`` exposes a SECOND family of
# resolvers used by ``model-registry.ts::getApiKeyAndHeaders`` /
# ``getApiKeyForProvider`` / ``getProviderAuthStatus``. These differ from
# :func:`resolve_config_value` (the Sprint 6e auth-storage helper) in two
# Pi-faithful ways:
#
# 1. **Non-raising shell exec, over an uncached primitive.** Pi's
#    ``executeWithDefaultShell`` catches every error (incl. non-zero exit)
#    and returns ``undefined``; the registry's ``getApiKeyAndHeaders`` wraps
#    the whole resolution in a try/catch and reports ``{ok: false, error}``.
#    The Sprint 6e helper instead used ``check=True`` (raises
#    ``CalledProcessError``), and that raise-vs-``None`` contract is what
#    Sprint 6e rejected for this path, which must surface a clean "Failed to
#    resolve …" message. So the command branch here returns :data:`None` on
#    any failure/empty output. What Sprint 6e ALSO rejected — a per-instance
#    cache — is now opt-in on the strict wrapper only (#240): the primitives
#    (:func:`_execute_command_uncached`, :func:`resolve_config_value_uncached`)
#    stay uncached, successes only are stored, and the key is the FULL
#    ``"!cmd"`` string where the Sprint 6e helper keys on ``value[1:]``. That
#    is a different key for every command a user would write, but NOT a
#    disjointness proof — ``value[1:]`` ranges over every string, so an
#    auth-family ``"!!cmd"`` lands on the strict key ``"!cmd"``. **Do not hand
#    one dict to both families**: nothing in the shipped wiring does (the
#    registry builds its own), and T5b pins what would happen if it did —
#    #242's cached ``""`` read back as a credential.
# 2. **Empty env → literal.** Pi uses ``process.env[config] || config``
#    (empty/unset env var falls back to the literal). The Sprint 6e helper
#    used ``os.environ.get(value, value)`` which returns ``""`` for an env
#    var set to the empty string; the ``or value`` form below matches Pi.


def _execute_command_uncached(
    value: str, *, failure: _Failure | None = None
) -> str | None:
    """Pi parity: ``executeCommandUncached`` → ``executeWithDefaultShell``.

    Runs ``value[1:]`` under the resolved shell — ``sh -c`` on POSIX, the first
    candidate of the win32 chain that spawns (#227) — and returns the trimmed
    stdout, or :data:`None` on a non-zero exit, timeout, output-cap overflow, OS
    error, or empty output. Never raises (matches Pi's
    ``try { execSync } catch { undefined }``).

    ``failure`` is the optional seat a named cause rides back in; the return
    value stays exactly what Pi's does.
    """

    result = _run_shell_command(value[1:], failure=failure)
    if result is None or result[0] != 0:
        return None
    out = result[1].strip()
    return out or None


def resolve_config_value_uncached(
    value: str, *, failure: _Failure | None = None
) -> str | None:
    """Pi parity: ``resolve-config-value.ts::resolveConfigValueUncached``.

    - ``!<command>`` → :func:`_execute_command_uncached` (``str`` or
      :data:`None`).
    - otherwise → the matching environment variable's value if set and
      non-empty, else the literal ``value``. Never :data:`None` for the
      env/literal branch (Pi ``process.env[config] || config``).

    ``failure`` is optional and keyword-only, so every existing call site keeps
    its signature and its "never raises" contract.
    """

    if value.startswith("!"):
        return _execute_command_uncached(value, failure=failure)
    return os.environ.get(value) or value


def resolve_config_value_or_throw(
    value: str, description: str, *, cache: dict[str, str] | None = None
) -> str:
    """Pi parity: ``resolve-config-value.ts::resolveConfigValueOrThrow``.

    Resolves ``value`` through the uncached primitive, optionally memoising a
    successful ``!command`` in ``cache`` (#240). Pi's ``resolveConfigValueOrThrow``
    is uncached at HEAD, so this is a deliberate divergence (ADR-0235). Raises
    :class:`ValueError` (Pi throws an ``Error``) with a Pi-verbatim message when
    a ``!command`` produced no output, or a generic message otherwise. The
    env/literal branch always resolves, so only the command branch can raise
    here.

    ``cache`` is keyword-only, so every existing call site keeps its signature,
    and opt-in, so there is no process-global credential store. Its owner is
    :class:`ModelRegistry`, which builds its own dict and clears it on every
    load; nothing here evicts. Do not share that dict with
    :func:`resolve_config_value` — see the comment below the docstring.
    Only a ``!command`` that SUCCEEDED with non-empty output is stored — a
    failure, a timeout, a terminal stop (#226) and empty output all raise below
    and leave the dict untouched, so a helper that starts working is picked up
    on the next request and #242's ``""`` hole is not widened.

    When the command was STOPPED by the terminal (#226) the Pi-verbatim message
    stays as the PREFIX and the named cause is appended after an em dash. With
    no cause — every other failure, and every failure on win32, where the
    detector is inert — the message is byte-for-byte what it has always been,
    with no dangling separator.
    """

    # The key is the FULL ``value``, leading ``!`` included — deliberately
    # unlike :func:`resolve_config_value`, which keys on ``value[1:]``. The two
    # families must not SHARE a dict: they disagree about empty output (that
    # one stores ``""``, this one must not) and about raising, and a refactor
    # that unified the attribute names must not silently unify these. The
    # differing key is what keeps a shared dict harmless for ordinary commands,
    # not a guarantee — ``value[1:]`` reaches every string, so an auth-family
    # ``"!!cmd"`` writes the strict key ``"!cmd"`` (measured in the #240 review,
    # pinned by T5b). The separation that holds is structural: each owner builds
    # its own dict.
    if cache is not None and value.startswith("!"):
        hit = cache.get(value)
        if hit is not None:
            return hit

    failure = _Failure()
    resolved = resolve_config_value_uncached(value, failure=failure)
    if resolved is not None:
        if cache is not None and value.startswith("!"):
            cache[value] = resolved
        return resolved
    if value.startswith("!"):
        message = f"Failed to resolve {description} from shell command: {value[1:]}"
        if failure.reason is not None:
            message = f"{message} — {failure.reason}"
        raise ValueError(message)
    raise ValueError(f"Failed to resolve {description}")


def resolve_headers_or_throw(
    headers: dict[str, str] | None,
    description: str,
    *,
    cache: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Pi parity: ``resolve-config-value.ts::resolveHeadersOrThrow``.

    Resolves every header VALUE via :func:`resolve_config_value_or_throw`
    (so a header may itself be ``!cmd`` or an env-var name). Returns the
    resolved mapping, or :data:`None` when ``headers`` is falsy or resolves
    empty.

    ``cache`` is threaded straight through (#240): headers are two of the three
    per-request resolution sites, so leaving them out would have left most of
    the cost in place. Two header names that share one ``!command`` therefore
    fork once.
    """

    if not headers:
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = resolve_config_value_or_throw(
            value, f'{description} header "{key}"', cache=cache
        )
    return resolved or None


__all__ = [
    "resolve_config_value",
    "resolve_config_value_or_throw",
    "resolve_config_value_uncached",
    "resolve_headers_or_throw",
]
