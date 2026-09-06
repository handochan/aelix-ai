"""Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).

Stored configuration values in Pi's ``auth.json`` can use two
indirection forms:

- ``!<command>``: the rest of the string is executed as a shell
  command via ``sh -c <command>``; the trimmed stdout becomes the
  resolved value. Per-command results are cached so repeated reads do
  not re-fork the shell.
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
from dataclasses import dataclass
from typing import cast

from aelix_ai.utils._process_tree import (
    ProcessTree,
    _resolve_platform,
    _retained_handle,
    containment_spawn_kwargs,
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

    def __init__(self, returncode: int, cmd: list[str], reason: str) -> None:
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


def _run_shell_command(
    cmd: str, *, failure: _Failure | None = None
) -> tuple[int, str] | None:
    """Run ``sh -c cmd`` with a wall-clock timeout AND a ~1 MB output cap.

    Mirrors Pi's ``execSync`` (``timeout: 10000`` + the implicit ~1 MB
    ``maxBuffer`` that throws ``ENOBUFS`` on overflow). Returns
    ``(returncode, stdout_text)`` or :data:`None` on a spawn error,
    timeout, or output-cap overflow — so a runaway producer can no longer
    OOM/hang the host. ``stderr`` is discarded (only stdout is consumed).

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
    """

    try:
        # The cast is about the CHECKER. ``containment_spawn_kwargs()`` is a
        # ``dict[str, Any]``, and unpacking one costs pyright its ability to
        # discriminate ``Popen``'s text/bytes overloads: it settles on
        # ``Popen[str]`` (measured — ``text=False`` does not steer it back), and
        # the byte-counting reader below would then read as a type error instead
        # of as the output cap it is. At runtime ``stdout=PIPE`` with no
        # ``text``/``encoding`` is bytes, which is what the cast asserts.
        proc = cast(
            "subprocess.Popen[bytes]",
            subprocess.Popen(  # noqa: S602 — intentional Pi-parity shell exec.
                ["sh", "-c", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                **containment_spawn_kwargs(),
            ),
        )
    except (OSError, ValueError):
        return None

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

        return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")
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
            argv = ["sh", "-c", cmd]
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
        out = result[1].rstrip("\n")
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
# 1. **Uncached + non-raising shell exec.** Pi's ``executeWithDefaultShell``
#    catches every error (incl. non-zero exit) and returns ``undefined``;
#    the registry's ``getApiKeyAndHeaders`` wraps the whole resolution in a
#    try/catch and reports ``{ok: false, error}``. The Sprint 6e helper
#    instead used ``check=True`` (raises ``CalledProcessError``) and a
#    per-instance cache — correct for auth.json but NOT the registry path,
#    which must surface a clean "Failed to resolve …" message. So the
#    command branch here returns :data:`None` on any failure/empty output.
# 2. **Empty env → literal.** Pi uses ``process.env[config] || config``
#    (empty/unset env var falls back to the literal). The Sprint 6e helper
#    used ``os.environ.get(value, value)`` which returns ``""`` for an env
#    var set to the empty string; the ``or value`` form below matches Pi.


def _execute_command_uncached(
    value: str, *, failure: _Failure | None = None
) -> str | None:
    """Pi parity: ``executeCommandUncached`` → ``executeWithDefaultShell``.

    Runs ``value[1:]`` via ``sh -c`` and returns the trimmed stdout, or
    :data:`None` on a non-zero exit, timeout, output-cap overflow, OS
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


def resolve_config_value_or_throw(value: str, description: str) -> str:
    """Pi parity: ``resolve-config-value.ts::resolveConfigValueOrThrow``.

    Resolves ``value`` uncached. Raises :class:`ValueError` (Pi throws an
    ``Error``) with a Pi-verbatim message when a ``!command`` produced no
    output, or a generic message otherwise. The env/literal branch always
    resolves, so only the command branch can raise here.

    When the command was STOPPED by the terminal (#226) the Pi-verbatim message
    stays as the PREFIX and the named cause is appended after an em dash. With
    no cause — every other failure, and every failure on win32, where the
    detector is inert — the message is byte-for-byte what it has always been,
    with no dangling separator.
    """

    failure = _Failure()
    resolved = resolve_config_value_uncached(value, failure=failure)
    if resolved is not None:
        return resolved
    if value.startswith("!"):
        message = f"Failed to resolve {description} from shell command: {value[1:]}"
        if failure.reason is not None:
            message = f"{message} — {failure.reason}"
        raise ValueError(message)
    raise ValueError(f"Failed to resolve {description}")


def resolve_headers_or_throw(
    headers: dict[str, str] | None, description: str
) -> dict[str, str] | None:
    """Pi parity: ``resolve-config-value.ts::resolveHeadersOrThrow``.

    Resolves every header VALUE via :func:`resolve_config_value_or_throw`
    (so a header may itself be ``!cmd`` or an env-var name). Returns the
    resolved mapping, or :data:`None` when ``headers`` is falsy or resolves
    empty.
    """

    if not headers:
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = resolve_config_value_or_throw(
            value, f'{description} header "{key}"'
        )
    return resolved or None


__all__ = [
    "resolve_config_value",
    "resolve_config_value_or_throw",
    "resolve_config_value_uncached",
    "resolve_headers_or_throw",
]
