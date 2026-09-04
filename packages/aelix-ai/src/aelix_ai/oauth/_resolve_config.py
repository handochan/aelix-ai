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
import subprocess
import threading
from typing import cast

from aelix_ai.utils._process_tree import (
    ProcessTree,
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


def _run_shell_command(cmd: str) -> tuple[int, str] | None:
    """Run ``sh -c cmd`` with a wall-clock timeout AND a ~1 MB output cap.

    Mirrors Pi's ``execSync`` (``timeout: 10000`` + the implicit ~1 MB
    ``maxBuffer`` that throws ``ENOBUFS`` on overflow). Returns
    ``(returncode, stdout_text)`` or :data:`None` on a spawn error,
    timeout, or output-cap overflow — so a runaway producer can no longer
    OOM/hang the host. ``stderr`` is discarded (only stdout is consumed).

    CONTAINMENT (#202, ADR-0238). ``proc.kill()`` reached the shell and nothing
    else: measured on ``main`` 39549b9, ``sh -c "a | b"`` keeps every stage of
    the pipeline in the shell's group, so a timed-out ``!command`` left them
    running (``sh -c "sleep 5"`` hid it — one command is ``exec``'d, so killing
    the shell IS killing it), and on Windows an MSYS ``sh.exe`` is an exec stub
    whose death orphans the command outright. The spawn now asks for a tree of
    its own and both kill sites end the tree
    (:mod:`aelix_ai.utils._process_tree`).

    The kwarg is ``process_group=0`` and NOT ``start_new_session=True``: this is
    the site where credential helpers run (``gpg``, ``pass``, pinentry) and they
    open ``/dev/tty``. ``setsid`` drops the controlling terminal — measured
    under a real pty, the helper answers ``sh: /dev/tty: Device not
    configured``. ``setpgid(0, 0)`` gives a new group inside the SAME session,
    which keeps the terminal and which ``killpg`` reaches identically.
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
        reader.join(_COMMAND_TIMEOUT)

        if reader.is_alive() or overflow:
            # Timed out, or exceeded the output cap — kill and fail. Straight to
            # the hard kill: there is no grace stage here to escalate from, and
            # the producer we are killing is by definition not answering.
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
        result = _run_shell_command(cmd)
        if result is None or result[0] != 0:
            # Preserve the raise-on-failure contract the auth.json cascade
            # relied on (was ``check=True``); a timeout or output-cap
            # overflow now fails here instead of hanging / OOMing.
            raise subprocess.CalledProcessError(
                result[0] if result is not None else -1, ["sh", "-c", cmd]
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


def _execute_command_uncached(value: str) -> str | None:
    """Pi parity: ``executeCommandUncached`` → ``executeWithDefaultShell``.

    Runs ``value[1:]`` via ``sh -c`` and returns the trimmed stdout, or
    :data:`None` on a non-zero exit, timeout, output-cap overflow, OS
    error, or empty output. Never raises (matches Pi's
    ``try { execSync } catch { undefined }``).
    """

    result = _run_shell_command(value[1:])
    if result is None or result[0] != 0:
        return None
    out = result[1].strip()
    return out or None


def resolve_config_value_uncached(value: str) -> str | None:
    """Pi parity: ``resolve-config-value.ts::resolveConfigValueUncached``.

    - ``!<command>`` → :func:`_execute_command_uncached` (``str`` or
      :data:`None`).
    - otherwise → the matching environment variable's value if set and
      non-empty, else the literal ``value``. Never :data:`None` for the
      env/literal branch (Pi ``process.env[config] || config``).
    """

    if value.startswith("!"):
        return _execute_command_uncached(value)
    return os.environ.get(value) or value


def resolve_config_value_or_throw(value: str, description: str) -> str:
    """Pi parity: ``resolve-config-value.ts::resolveConfigValueOrThrow``.

    Resolves ``value`` uncached. Raises :class:`ValueError` (Pi throws an
    ``Error``) with a Pi-verbatim message when a ``!command`` produced no
    output, or a generic message otherwise. The env/literal branch always
    resolves, so only the command branch can raise here.
    """

    resolved = resolve_config_value_uncached(value)
    if resolved is not None:
        return resolved
    if value.startswith("!"):
        raise ValueError(
            f"Failed to resolve {description} from shell command: {value[1:]}"
        )
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
