"""Sprint 6e W6 (P-141) — ``resolve_config_value`` helper tests.

Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth._resolve_config import (
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)
from aelix_ai.utils._shell import POWERSHELL_NAMES, shell_basename

from tests.process_probe import STATE_ALIVE, is_dead_or_zombie, probe_state


def test_literal_passes_through_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal that does NOT match any env var name returns verbatim."""

    monkeypatch.delenv("ZZ_TOTALLY_FICTIONAL_KEY", raising=False)
    assert resolve_config_value("ZZ_TOTALLY_FICTIONAL_KEY") == (
        "ZZ_TOTALLY_FICTIONAL_KEY"
    )


def test_env_var_indirection(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the literal matches an env var name, the env value substitutes."""

    monkeypatch.setenv("MY_INDIRECTED_KEY", "sk-from-env")
    assert resolve_config_value("MY_INDIRECTED_KEY") == "sk-from-env"


def test_shell_command_indirection() -> None:
    """``!<cmd>`` runs the shell command + returns trimmed stdout."""

    out = resolve_config_value("!echo sk-from-shell")
    assert out == "sk-from-shell"


def test_shell_command_indirection_is_cached() -> None:
    """The cache short-circuits repeat invocations."""

    cache: dict[str, str] = {}
    out1 = resolve_config_value("!echo first-call", cache)
    # Pre-seed a different value into the cache to prove the cache wins
    # over re-running the command.
    cache["echo first-call"] = "cached-value"
    out2 = resolve_config_value("!echo first-call", cache)
    assert out1 == "first-call"
    assert out2 == "cached-value"


def test_shell_command_strips_trailing_newline() -> None:
    """Pi trims the trailing newline from shell output."""

    out = resolve_config_value("!printf 'sk-no-newline\\n'")
    assert out == "sk-no-newline"


def test_no_cache_argument_re_executes_each_call(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With no cache, each call re-forks — proven by a counter file that
    increments per invocation (a cached value would stay "1")."""

    counter = tmp_path / "n"
    cmd = f"!printf x >> {counter}; wc -c < {counter} | tr -d ' '"
    out1 = resolve_config_value(cmd)
    out2 = resolve_config_value(cmd)
    assert out1 == "1"
    assert out2 == "2"  # re-executed, not cached


# === ADR-0140 review hardening — bounded output + timeout =====================


def test_uncached_command_output_cap_returns_none() -> None:
    """A runaway producer exceeds the ~1 MB cap and is killed → None
    (mirrors Pi's execSync ENOBUFS→undefined; prevents OOM)."""

    assert resolve_config_value_uncached("!yes") is None


def test_uncached_command_timeout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.oauth._resolve_config as rc

    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", 0.3)
    assert resolve_config_value_uncached("!sleep 5") is None


def test_cached_command_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    import aelix_ai.oauth._resolve_config as rc

    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", 0.3)
    with pytest.raises(subprocess.CalledProcessError):
        resolve_config_value("!sleep 5")


# === P0 #4 (ADR-0140) — request-time wrappers ==================================
# Pi parity: resolveConfigValueUncached / resolveConfigValueOrThrow /
# resolveHeadersOrThrow. Differ from resolve_config_value: command failures
# return None (not raise), empty env falls back to the literal.


def test_uncached_literal_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZZ_FICTIONAL_UNCACHED", raising=False)
    assert resolve_config_value_uncached("ZZ_FICTIONAL_UNCACHED") == (
        "ZZ_FICTIONAL_UNCACHED"
    )


def test_uncached_empty_env_falls_back_to_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pi ``process.env[config] || config`` — an env var set to "" → literal.
    monkeypatch.setenv("EMPTY_ENV_KEY", "")
    assert resolve_config_value_uncached("EMPTY_ENV_KEY") == "EMPTY_ENV_KEY"


def test_uncached_env_indirection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SET_ENV_KEY", "resolved")
    assert resolve_config_value_uncached("SET_ENV_KEY") == "resolved"


def test_uncached_command_returns_value() -> None:
    assert resolve_config_value_uncached("!printf done") == "done"


def test_uncached_failed_command_returns_none() -> None:
    # Non-zero exit / empty output → None (Pi catches + returns undefined).
    assert resolve_config_value_uncached("!false") is None
    assert resolve_config_value_uncached("!true") is None


def test_or_throw_returns_resolved_value() -> None:
    assert resolve_config_value_or_throw("!printf k", "test") == "k"


def test_or_throw_raises_for_failed_command() -> None:
    with pytest.raises(ValueError, match="from shell command: false"):
        resolve_config_value_or_throw("!false", "API key for x")


def test_headers_or_throw_resolves_each_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HDR_ENV", "hdr-secret")
    out = resolve_headers_or_throw(
        {"X-Lit": "literalval", "X-Env": "HDR_ENV"}, "provider \"x\""
    )
    assert out == {"X-Lit": "literalval", "X-Env": "hdr-secret"}


def test_headers_or_throw_none_returns_none() -> None:
    assert resolve_headers_or_throw(None, "x") is None
    assert resolve_headers_or_throw({}, "x") is None


def test_headers_or_throw_raises_on_failed_command() -> None:
    with pytest.raises(ValueError, match='header "X-Bad"'):
        resolve_headers_or_throw({"X-Bad": "!false"}, 'provider "x"')


# === #202 / ADR-0238 — a ``!command`` is a tree, not just the shell ===========


#: The whole chain the timeout case has to build before the pid file exists:
#: ``sh`` starts, the interpreter starts, it spawns a grandchild and writes the
#: pid. Two interpreter startups on the windows leg, so the bound is 2.0 s and
#: not the sub-second one a "timeout" reflexively suggests — under a tighter one
#: the case would measure runner latency and fail before the kill it is about.
_TIMEOUT_S = 2.0

#: Seconds the grandchild gets to die before the case fails. Signal delivery is
#: scheduled by the kernel and CI runners are slow.
_DEADLINE = 5.0

#: ``sh`` on the windows leg is Git bash, which takes a forward-slashed path
#: without re-quoting it. On POSIX there is no backslash to replace.
_PYTHON = sys.executable.replace("\\", "/")

#: Spawns a grandchild that holds NO pipe of ours (so the reader still sees EOF
#: when the tree dies), records its pid, and then outlives the timeout.
#:
#: ``os.replace`` and not ``write_text``, because this is the one case whose
#: whole design is "the reader races the killer": ``write_text`` creates a
#: zero-byte file first, and a kill landing between the create and the write
#: leaves ``exists()`` True and ``int("")`` raising ``ValueError`` — an ERROR
#: instead of the diagnosable assertion the case is meant to produce
#: (win-leg/F7). The twin in tests/subprocess_hooks has always done it this way.
_PIPELINE_SOURCE = """\
import os
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
path = sys.argv[1]
with open(path + ".part", "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
os.replace(path + ".part", path)
time.sleep(60)
"""

#: Closes stdout at once and then LINGERS while holding a descendant — the
#: second hard-kill site in ``_run_shell_command`` (the ``TimeoutExpired`` from
#: ``proc.wait(timeout=1.0)``), which no case reached before. Mutation MUT-3
#: measured it: with that site reverted to ``proc.kill()`` the descendant is
#: orphaned and the whole suite stays green.
#:
#: ``os.close(1)`` and not ``sys.stdout.close()``: measured here, closing the
#: TextIOWrapper leaves the underlying descriptor open, so the reader waited out
#: the full ``_COMMAND_TIMEOUT`` and the case silently took the OTHER kill site.
_EOF_THEN_LINGER_SOURCE = """\
import os
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
path = sys.argv[1]
with open(path + ".part", "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
os.replace(path + ".part", path)
sys.stdout.flush()
os.close(1)
time.sleep(60)
"""


def _reap(pid: int) -> None:
    """Kill ``pid`` if it is still there. Cleanup only — never an assertion.

    Root-only on purpose: a case that leaks a pid must not clean up through the
    process group, which is the mechanism under test.

    On win32 ``taskkill.exe`` is resolved from ``%SystemRoot%\\System32`` with
    the bare name only as the retry, the same way the product's
    ``_taskkill_tree`` does it: ``System32`` is not always on ``PATH`` and a bare
    name then fails ``ENOENT`` (Pi #6596/#8560). Everything here is suppressed,
    so the bare spelling would leak a 60 s sleeper into the rest of the leg
    silently (win-leg/F8).
    """

    with contextlib.suppress(Exception):
        if is_dead_or_zombie(pid):
            return
        if sys.platform == "win32":
            root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
            for argv0 in (os.path.join(root, "System32", "taskkill.exe"), "taskkill"):
                try:
                    subprocess.run(
                        [argv0, "/F", "/PID", str(pid)], capture_output=True, check=False
                    )
                except FileNotFoundError:
                    continue
                return
        else:
            os.kill(pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]


def _await_dead(pid: int) -> str:
    """Poll until ``pid`` is dead, returning the LAST OBSERVED state.

    The state is the assertion subject so a failure says what it kept seeing.
    Never ``os.kill(pid, 0)``: on Windows that is a ``TerminateProcess`` and the
    probe would cause the death it claims to observe (#203).
    """

    deadline = time.monotonic() + _DEADLINE
    state = probe_state(pid)
    while state == STATE_ALIVE and time.monotonic() < deadline:
        time.sleep(0.05)
        state = probe_state(pid)
    return state


def test_a_timed_out_command_does_not_orphan_its_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeout kill reaches what the ``!command`` spawned, not just ``sh``.

    On ``main`` 39549b9 this was ``proc.kill()``: the shell died and everything
    it had forked kept running (``sh -c "sleep 5"`` hid the bug, because one
    command is ``exec``'d and so IS the shell). Measured here with a real
    grandchild, which the process group holds on POSIX and the job on win32.
    """

    import aelix_ai.oauth._resolve_config as rc

    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", _TIMEOUT_S)
    argv = (_PYTHON, "-c", _PIPELINE_SOURCE, str(pidfile))
    command = " ".join(shlex.quote(part) for part in argv)

    assert resolve_config_value_uncached("!" + command) is None

    assert pidfile.exists(), (
        f"the command never reached its grandchild inside {_TIMEOUT_S}s — this "
        "case is measuring interpreter startup, not the kill"
    )
    grandchild = int(pidfile.read_text())
    try:
        state = _await_dead(grandchild)
        assert state != STATE_ALIVE, (
            f"the ``!command`` pipeline outlived its kill (state={state})"
        )
    finally:
        _reap(grandchild)


def test_a_command_that_closes_stdout_and_lingers_still_loses_its_tree(
    tmp_path: Path,
) -> None:
    """The SECOND hard-kill site: EOF on stdout, then a child that will not go.

    ``_run_shell_command`` has two kill sites and only the reader-timeout one was
    covered — the sibling case above patches ``_COMMAND_TIMEOUT`` and so never
    reaches this branch. Mutation MUT-3 reverted this site alone to
    ``proc.kill()`` and measured the descendant orphaned (``ALIVE`` after the
    reap) with the suite still green.

    ``_COMMAND_TIMEOUT`` is deliberately left at its production 10 s: the branch
    under test is the ``subprocess.TimeoutExpired`` from ``proc.wait(timeout=1.0)``
    AFTER the reader saw EOF, and only an unpatched timeout separates the two
    kill sites in the elapsed time. The lower bound below is asserted and the
    upper one is NOT: reaching the EOF branch needs the shell to have ``exec``'d
    the command (POSIX ``sh`` does; Git bash on the windows leg is not measured),
    and where it has not, the reader timeout takes the first kill site — still a
    containment assertion, just at the other rung, so a leg that lands there
    must not go red for it.
    """

    pidfile = tmp_path / "descendant.pid"
    argv = (_PYTHON, "-c", _EOF_THEN_LINGER_SOURCE, str(pidfile))
    command = " ".join(shlex.quote(part) for part in argv)

    started = time.monotonic()
    assert resolve_config_value_uncached("!" + command) is None
    elapsed = time.monotonic() - started

    assert elapsed >= 1.0, (
        f"resolve returned in {elapsed:.2f}s — the 1.0 s bounded reap never "
        "expired, so this case did not reach the second kill site"
    )
    assert pidfile.exists(), (
        "the command never reached its descendant — this case is measuring "
        "interpreter startup, not the kill"
    )
    descendant = int(pidfile.read_text())
    try:
        state = _await_dead(descendant)
        assert state != STATE_ALIVE, (
            f"the descendant outlived the EOF-then-linger kill (state={state})"
        )
    finally:
        _reap(descendant)


def test_the_command_tree_is_not_attached_with_kill_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kill_on_close`` is a per-SITE decision and nothing pinned this site.

    Mutation MUT-6: flipping the flag here left the whole suite green, though
    on win32 it would end a helper a successful ``!command`` deliberately
    backgrounded — the behaviour every platform has today.
    """

    import aelix_ai.oauth._resolve_config as rc

    seen: list[dict[str, Any]] = []
    real_attach = rc.ProcessTree.attach

    class _AttachSpy:
        @staticmethod
        def attach(pid: int, **kwargs: Any) -> Any:
            seen.append(dict(kwargs))
            return real_attach(pid, **kwargs)

    monkeypatch.setattr(rc, "ProcessTree", _AttachSpy)
    assert resolve_config_value_uncached("!printf released") == "released"

    assert len(seen) == 1
    assert seen[0].get("kill_on_close") is not True


def test_resolve_config_spawns_a_new_group_in_the_same_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``process_group=0``, never ``start_new_session=True``.

    The reason is narrower than this case first recorded (#226). A
    ``process_group=0`` child can OPEN ``/dev/tty`` but cannot READ it: its
    group is never the terminal's foreground group, so the kernel stops it. What
    the kwarg buys is that the failure is an observable STOP on a terminal the
    child keeps as its CONTROLLING terminal — where ``setsid`` was measured to
    produce silent theft instead (a helper that opens the terminal by path faces
    no job-control check at all and ate the line the user had typed), and where
    passing no kwarg at all lets the group-delivered signal stop Aelix too.
    ``killpg`` reaches every one of those shapes identically.

    "The tty consequence is not reproducible headless" was wrong, and is gone:
    ``test_a_helper_that_reads_the_terminal_fails_fast_and_names_the_cause`` in
    this file reproduces it on a runner with no controlling terminal, by giving
    a child one of its own. So this case now pins the SPAWN KWARG and nothing
    else; the consequence is pinned there.
    """

    seen: list[dict[str, Any]] = []
    real_popen = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    assert resolve_config_value_uncached("!printf grouped") == "grouped"

    assert len(seen) == 1
    kwargs = seen[0]
    # ``stdin`` is pinned HERE, ahead of the platform split, because it is the
    # one containment kwarg that is the same on every platform: a ``!command``
    # helper must never inherit a console to prompt on. Nothing behavioural can
    # see it — under pytest's fd capture fd 0 ALREADY is ``/dev/null``, so
    # deleting ``stdin=subprocess.DEVNULL`` from the spawn was measured to leave
    # the WHOLE suite bit-identical to baseline (``10279 passed, 12 skipped, 39
    # warnings``, 2026-09-08). Four shipped surfaces argue FROM this kwarg —
    # ADR-0238, the models-json guide, ``_POWERSHELL_HARDENING``'s docstring and
    # a test docstring — and every other spawn site in the repo pins it this way.
    assert kwargs.get("stdin") is subprocess.DEVNULL
    if sys.platform == "win32":
        # ``CREATE_NEW_PROCESS_GROUP``, spelled as the literal the product code
        # carries because the name does not exist off Windows at runtime.
        assert kwargs["creationflags"] & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        # ``CREATE_NO_WINDOW`` too, since #227: the child is a console app now
        # (a real PowerShell or cmd.exe, where the win32 spawn of ``sh`` used to
        # fail before an image loaded) and this process may have no console to
        # lend it, in which case ``CreateProcess`` would give it a new one.
        assert kwargs["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0x0800_0000)
    else:
        assert kwargs["process_group"] == 0
        assert "start_new_session" not in kwargs


# === #226 / ADR-0238 — the terminal stop a credential helper takes ============
#
# ``pty`` / ``termios`` / ``fcntl`` are deliberately NOT imported at module
# scope: none of the three exists on the windows leg, and an import here would
# take every case in this file down at collection — the exact regression
# ci.yml's windows leg is kept to catch ("the next ``fcntl`` import"). They live
# inside the child source strings below, which only a POSIX arm ever runs.

#: Upper bound on ONE resolve of a helper that touches the terminal. The
#: detector was measured at 0.052–0.059 s (darwin, and Linux/dash on py3.11 and
#: py3.12, root and non-root); ``main`` burns the whole 10 s ``_COMMAND_TIMEOUT``
#: instead. This bound sits ~35x above the detector and a fifth of the
#: timeout, so it measures the mechanism and not the runner.
_STOP_BOUND_S = 2.0

#: How long the PARENT waits for a pty child. Not an assertion — the assertions
#: are the child's own measurements, written to a file. Generous on purpose: on
#: ``main`` the first case's child needs ~20 s (two 10 s stalls), and the red
#: has to be the readable message assertion rather than a timeout.
_PTY_DEADLINE_S = 60.0

#: Runs the two resolvers that a credential helper reaches, under a real
#: controlling terminal, and records what each one said and how long it took.
#: ``TIOCSCTTY`` on the first line is what makes the pty a CONTROLLING terminal
#: for this child — without it the kernel's background-group test never applies
#: and the case would measure nothing. The pytest interpreter is never forked.
_TERMINAL_READ_SOURCE = """\
import fcntl
import json
import os
import sys
import termios
import time

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
out_path, path_json = sys.argv[1], sys.argv[2]
sys.path[:0] = json.loads(path_json)

from aelix_ai.oauth._resolve_config import (
    resolve_config_value,
    resolve_config_value_or_throw,
)

result = {
    "ctty": os.ttyname(0),
    "we_are_the_foreground_group": os.tcgetpgrp(0) == os.getpgrp(),
}

started = time.monotonic()
try:
    resolve_config_value_or_throw("!read x < /dev/tty", 'API key for provider "x"')
    result["or_throw"] = None
except Exception as exc:
    result["or_throw"] = str(exc)
result["or_throw_elapsed"] = time.monotonic() - started

started = time.monotonic()
try:
    resolve_config_value("!read x < /dev/tty")
    result["cached"] = None
except Exception as exc:
    result["cached"] = str(exc)
result["cached_elapsed"] = time.monotonic() - started

with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""

#: A helper that IGNORES ``SIGTTOU`` and then turns echo off, on its own
#: controlling terminal. POSIX permits it, so there is no stop to detect; the
#: command succeeds and the terminal keeps the setting. The pty is this case's
#: own, so nothing the runner owns is touched.
_IGNORED_SIGTTOU_SOURCE = """\
import fcntl
import json
import sys
import termios

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
out_path, path_json = sys.argv[1], sys.argv[2]
sys.path[:0] = json.loads(path_json)

import aelix_ai.oauth._resolve_config as rc

failure = rc._Failure()
outcome = rc._run_shell_command(
    "trap '' TTOU; stty -echo < /dev/tty; echo ignored", failure=failure
)
result = {
    "returncode": None if outcome is None else outcome[0],
    "stdout": None if outcome is None else outcome[1],
    "reason": failure.reason,
    "echo_is_off": not (termios.tcgetattr(0)[3] & termios.ECHO),
}
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""

#: The SIGTTOU half of the same rule, with the DEFAULT disposition — the same
#: child recipe as :data:`_IGNORED_SIGTTOU_SOURCE` minus the ``trap``. A helper
#: that turns echo off from a background group is stopped before ``tcsetattr``
#: applies, so the stop is detected AND the terminal keeps its echo. The pty is
#: this case's own, so nothing the runner owns is touched.
_TERMINAL_SETTINGS_SOURCE = """\
import fcntl
import json
import sys
import termios
import time

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
out_path, path_json = sys.argv[1], sys.argv[2]
sys.path[:0] = json.loads(path_json)

import aelix_ai.oauth._resolve_config as rc

failure = rc._Failure()
started = time.monotonic()
outcome = rc._run_shell_command(
    "stty -echo < /dev/tty; echo notstopped", failure=failure
)
result = {
    "elapsed": time.monotonic() - started,
    "outcome": outcome,
    "returncode": failure.returncode,
    "reason": failure.reason,
    "echo_is_off": not (termios.tcgetattr(0)[3] & termios.ECHO),
}
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""

#: The one shape in which the stop probe can reach an already-exited leader: a
#: grandchild inherits stdout and holds the pipe open for ~0.4 s, so the reader
#: thread is still alive and still polling after the root has exited. ``!exit 7``
#: cannot be used — its EOF and its exit land in the same tick and the loop
#: breaks before the first probe (measured: probes=0).
_REAP_RACE_SOURCE = """\
import subprocess
import sys

subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
raise SystemExit(7)
"""


def _resolve_under_a_new_terminal(
    out: Path, source: str, *args: str
) -> dict[str, Any]:
    """Run ``source`` in a child whose stdin IS its controlling terminal.

    ``os.openpty()`` + ``start_new_session=True`` + ``TIOCSCTTY`` in the child.
    The pytest interpreter is NOT forked: ``pty.fork`` would make the parent an
    orphaned session leader, where ``SIGTTIN`` turns into an ``EIO`` on the read
    and the case would silently measure the wrong thing.

    The result comes back through a file, not the terminal: a pty echoes what is
    written to it and translates newlines, so parsing its output would be
    parsing our own echo. Terminal output is drained anyway, and only so a child
    that writes more than a pty buffer cannot wedge, and so a failure can quote
    what it saw.
    """

    master, slave = os.openpty()
    argv = [_PYTHON, "-c", source, str(out), json.dumps(sys.path), *args]
    proc = subprocess.Popen(
        argv,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    seen: list[bytes] = []

    def _drain() -> None:
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:
                return
            if not data:
                return
            seen.append(data)

    pump = threading.Thread(target=_drain, daemon=True)
    pump.start()
    try:
        proc.wait(timeout=_PTY_DEADLINE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=_DEADLINE)
    finally:
        pump.join(_DEADLINE)
        with contextlib.suppress(OSError):
            os.close(master)

    assert out.exists(), (
        f"the child under the pty never wrote its result (rc={proc.returncode}); "
        f"it said: {b''.join(seen).decode('utf-8', 'replace')[-2000:]!r}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_a_helper_that_reads_the_terminal_fails_fast_and_names_the_cause(
    tmp_path: Path,
) -> None:
    """``!read x < /dev/tty`` is STOPPED, and both resolvers say so.

    A ``!command`` runs in a process group of its own, which is never the
    terminal's foreground group, so the kernel raises ``SIGTTIN`` the moment the
    helper reads the terminal. On ``main`` that cost the full 10 s
    ``_COMMAND_TIMEOUT`` and the message said nothing about a terminal.

    The cached resolver is the one an ``auth.json`` ``key`` reaches
    (``AuthStorage.get_api_key`` / ``get_api_key_cascade`` are its only
    production callers), so the message is asserted not to name ``models.json``
    — and to carry the ``SIGKILL`` we actually sent rather than the ``SIGHUP``
    that a ``-1`` returncode renders as, which would name two signals in one
    breath.

    The windows arm is not this one twice: there is no ``WUNTRACED`` and no
    background process group there, the detector is inert by design, and what
    that leg pins is the branch this change introduces for it — a failure with
    no reason keeps today's Pi-verbatim string and grows no dangling ``—``.
    """

    if sys.platform == "win32":
        with pytest.raises(ValueError) as excinfo:
            resolve_config_value_or_throw("!exit 3", 'API key for provider "x"')
        message = str(excinfo.value)
        assert message == (
            'Failed to resolve API key for provider "x" from shell command: exit 3'
        )
        assert "—" not in message
        return

    result = _resolve_under_a_new_terminal(
        tmp_path / "stop.json", _TERMINAL_READ_SOURCE
    )
    assert result["we_are_the_foreground_group"], (
        f"the child did not take {result['ctty']} as its foreground terminal, so "
        "the helper's group was not a background one and this case measured "
        "nothing"
    )

    or_throw = result["or_throw"]
    cached = result["cached"]
    assert or_throw is not None and cached is not None, (
        f"the terminal read resolved instead of being stopped: {result}"
    )
    assert or_throw.startswith(
        'Failed to resolve API key for provider "x" from shell command: '
        "read x < /dev/tty"
    ), or_throw
    assert "stopped reading the terminal (SIGTTIN)" in or_throw, or_throw
    assert " — " in or_throw, or_throw
    assert "stopped reading the terminal (SIGTTIN)" in cached, cached
    assert "SIGKILL" in cached, cached
    assert "SIGHUP" not in cached, cached
    assert "models.json" not in or_throw and "models.json" not in cached
    assert result["or_throw_elapsed"] < _STOP_BOUND_S, result
    assert result["cached_elapsed"] < _STOP_BOUND_S, result
    warnings.warn(
        "#226 terminal stop under a real pty: "
        f"or_throw {result['or_throw_elapsed']:.3f}s, "
        f"cached {result['cached_elapsed']:.3f}s "
        f"(bound {_STOP_BOUND_S}s)",
        stacklevel=1,
    )


def test_the_terminal_stop_detector_is_posix_only_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detector is a POSIX mechanism and does not pretend otherwise.

    On win32 there is no ``WUNTRACED``, no background process group and no
    job-control stop, so the probe must not even be attempted: an injected
    ``platform="win32"`` returns ``None`` without reaching ``os.waitpid``, which
    a spy proves by raising if it is called.

    The host arm then pins the other half — that a RUNNING child is not
    mistaken for a stopped one — on whichever leg is running it.
    """

    import aelix_ai.oauth._resolve_config as rc

    class _Unreachable:
        pid = -1

    def _explode(*args: Any, **kwargs: Any) -> tuple[int, int]:
        raise AssertionError("the win32 arm must not reach os.waitpid")

    monkeypatch.setattr(os, "waitpid", _explode)
    assert rc._stopped_by_the_terminal(_Unreachable(), platform="win32") is None
    monkeypatch.undo()

    if sys.platform == "win32":
        assert rc._WAIT_STOP_OPTIONS == 0
    else:
        assert rc._WAIT_STOP_OPTIONS != 0

    proc = subprocess.Popen(
        ["sh", "-c", "exec sleep 5"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        **rc.containment_spawn_kwargs(),
    )
    try:
        assert rc._stopped_by_the_terminal(proc) is None
        assert proc.returncode is None, (
            "the probe wrote an exit code onto a child that is still running"
        )
    finally:
        proc.kill()
        proc.wait(timeout=_DEADLINE)


def test_a_probe_that_reaps_an_exited_command_does_not_lose_its_exit_code() -> None:
    """A helper that failed must not be reported as one that succeeded.

    The stop probe calls ``os.waitpid`` on a pid ``Popen`` still expects to reap.
    When the leader has already exited, the probe takes the status ``Popen``
    wanted, and ``Popen._try_wait`` swallows the resulting ``ChildProcessError``
    as ``sts = 0`` — measured: exit 7 read back as exit 0, a failed credential
    helper reported as a successful one whose key is the empty string. The probe
    therefore hands the status back through ``os.waitstatus_to_exitcode``.

    The assertion is the same on every leg and the asymmetry is here rather than
    in a branch: on POSIX the probe reaps and the repair line is what makes this
    7, and on win32 the detector never runs, so ``Popen`` reaps it itself and the
    7 arrives the way it always did.
    """

    argv = (_PYTHON, "-c", _REAP_RACE_SOURCE)
    command = " ".join(shlex.quote(part) for part in argv)

    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        resolve_config_value("!" + command)
    elapsed = time.monotonic() - started

    assert excinfo.value.returncode == 7, (
        "the exit code of a command whose leader exited while a grandchild held "
        "stdout was lost"
    )
    warnings.warn(
        f"#226 reap-repair through _run_shell_command: {elapsed:.3f}s", stacklevel=1
    )


def test_a_non_terminal_stop_is_not_named_and_still_costs_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the two TERMINAL stops are named. ``SIGSTOP`` is not one of them.

    A helper stopped for a reason that has nothing to do with the terminal has
    nothing to tell the user about the terminal, so it keeps today's behaviour:
    the full ``_COMMAND_TIMEOUT`` and an unnamed failure.

    The mapping is split on ``sys.platform`` rather than on an injected value
    because it is built at IMPORT time — ``monkeypatch.delattr(signal, …)``
    produces a false failure (measured: ``{21, 22} == set()``). ``signal.SIGSTOP``
    is referenced only inside the POSIX arm: Windows ``SIGNAL.H`` has no
    ``SIGSTOP`` and CPython exposes the name only under ``#ifdef``, so an
    unconditional reference is an ``AttributeError`` on the gating leg — while
    the ``getattr(signal, "SIGSTOP", None) not in …`` spelling that avoids it
    passes vacuously there (``None not in {}``).
    """

    import aelix_ai.oauth._resolve_config as rc

    if sys.platform == "win32":
        assert rc._TERMINAL_STOP_SIGNALS == {}
        return

    assert set(rc._TERMINAL_STOP_SIGNALS) == {signal.SIGTTIN, signal.SIGTTOU}
    assert signal.SIGSTOP not in rc._TERMINAL_STOP_SIGNALS

    monkeypatch.setattr(rc, "_COMMAND_TIMEOUT", 1.0)
    started = time.monotonic()
    assert resolve_config_value_uncached("!kill -STOP $$; sleep 30") is None
    elapsed = time.monotonic() - started

    assert elapsed >= 1.0, (
        f"a self-stopped command returned in {elapsed:.2f}s — it was named as a "
        "terminal stop, which it is not"
    )
    warnings.warn(
        f"#226 non-terminal stop still costs the timeout: {elapsed:.3f}s",
        stacklevel=1,
    )


def test_a_helper_that_ignores_sigttou_is_not_detected(tmp_path: Path) -> None:
    """The rule is conditional, and this is the condition.

    POSIX lets a process ignore ``SIGTTOU``; a helper that does can turn echo off
    from a background group and the kernel raises nothing. There is no stop, so
    there is nothing to detect and nothing to name — the command SUCCEEDS and the
    terminal keeps the setting. ADR-0238's amendment and the guide both state the
    rule with that "unless", and this case is what holds them to it.

    It runs on a pty of its own, so the setting it leaves off belongs to a
    terminal nobody else has.

    The windows arm is thin because the boundary cannot exist there: with no
    ``WUNTRACED`` and no background process group, ignoring a signal that is
    never raised changes nothing. What that leg pins is that the detector is
    inert rather than wrong.
    """

    import aelix_ai.oauth._resolve_config as rc

    if sys.platform == "win32":
        assert rc._WAIT_STOP_OPTIONS == 0
        assert rc._TERMINAL_STOP_SIGNALS == {}
        return

    result = _resolve_under_a_new_terminal(
        tmp_path / "ignored.json", _IGNORED_SIGTTOU_SOURCE
    )
    assert result["reason"] is None, result
    assert result["returncode"] == 0, result
    assert "ignored" in (result["stdout"] or ""), result
    assert result["echo_is_off"], (
        "the helper never turned echo off, so this case did not reach the hole "
        "it is here to record"
    )


def test_a_helper_that_changes_the_terminal_is_stopped_and_named(tmp_path: Path) -> None:
    """The ``SIGTTOU`` half of the rule, and the half a per-signal mutant drops.

    ``SIGTTIN`` and ``SIGTTOU`` come from two different kernel checks, and only
    the first has a case of its own above. A detector that maps ``SIGTTIN`` and
    returns :data:`None` for ``SIGTTOU`` restores this issue's exact symptom on
    the ``stty`` path — a 10 s stall with no named cause, which the amendment,
    the CHANGELOG and the guide all describe as fixed — while leaving the WHOLE
    suite green (measured: 10242 passed with that mutant live). This case is
    what makes it red.

    ``not echo_is_off`` is the user-visible half of the same assertion: the stop
    has to arrive BEFORE ``tcsetattr`` applies, or the command fails AND leaves
    the user's terminal mute. Case 5 is this helper with ``SIGTTOU`` ignored,
    and there the echo does go off — the two together are the "unless it blocks
    or ignores it" that ADR-0238 and the guide both state.

    The windows arm is the inert pair case 5 already pins: with no ``WUNTRACED``
    and no background process group there is no stop to name.
    """

    import aelix_ai.oauth._resolve_config as rc

    if sys.platform == "win32":
        assert rc._WAIT_STOP_OPTIONS == 0
        assert rc._TERMINAL_STOP_SIGNALS == {}
        return

    result = _resolve_under_a_new_terminal(
        tmp_path / "settings.json", _TERMINAL_SETTINGS_SOURCE
    )
    assert result["outcome"] is None, (
        f"the helper changed the terminal and succeeded anyway: {result}"
    )
    reason = result["reason"]
    assert reason is not None, (
        f"the command failed with no named cause — the 10 s unnamed stall this "
        f"issue is about: {result}"
    )
    assert "changing the terminal's settings (SIGTTOU)" in reason, reason
    assert result["returncode"] == -9, result
    assert result["elapsed"] < _STOP_BOUND_S, result
    assert not result["echo_is_off"], (
        "the stop landed after tcsetattr applied, so the helper was killed AND "
        "the user's terminal was left with echo off"
    )
    warnings.warn(
        f"#226 SIGTTOU stop under a real pty: {result['elapsed']:.3f}s "
        f"(bound {_STOP_BOUND_S}s)",
        stacklevel=1,
    )


# === #227 / ADR-0238 — a ``!command`` resolves a shell on win32 ===============
#
# Every case here runs on every leg. The win32 answers are driven by injecting
# ``platform="win32"`` and an ``env`` mapping rather than by patching
# ``sys.platform``, because ``shutil.which`` itself branches on the global and
# then calls ``_winapi`` (``None`` off Windows) — patching it would crash the
# very PATH probe under test. Injection is the shape
# ``tests/tools/test_bash_shell_win32.py`` already uses for the same chain.
#
# EVERY injected env spells ``PATH``. With the key absent the primitive skips
# PATH probing entirely, so a row meant as "stock Windows" would silently make
# no PATH candidates at all rather than finding this box's ``/bin/sh`` — a
# quiet under-assertion either way, and the rule closes both.


def _executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _on_path(directory: Path, name: str) -> Path:
    """A PATH probe target ``shutil.which(name)`` can actually find here.

    Windows' ``shutil.which`` only tries ``name + PATHEXT`` candidates and never
    the bare name, so an extensionless fixture is unreachable there and the win32
    arm would slide past it. Same helper shape, and same measured reason, as
    ``tests/tools/test_bash_shell_win32.py``'s.
    """

    return _executable(directory, f"{name}.exe" if sys.platform == "win32" else name)


def _record_spawns(
    monkeypatch: pytest.MonkeyPatch,
    errors: Sequence[BaseException | None] | None = None,
) -> list[Any]:
    """Every argv ``_run_shell_command`` hands :class:`subprocess.Popen`.

    ``errors[i]``, when given and not :data:`None`, is raised INSTEAD of the
    i-th spawn. That is how the fall-through cases stage a candidate that
    cannot run: the win32 errors they are about (``ERROR_BAD_EXE_FORMAT`` →
    ``ENOEXEC``) have no POSIX fixture that produces them on both legs.

    The list's LAST entry is the argv that actually spawned whenever the call
    returned a value, because the loop breaks on its first success.
    """

    seen: list[Any] = []
    real_popen = subprocess.Popen

    def spy(argv: Any, *args: Any, **kwargs: Any) -> Any:
        index = len(seen)
        seen.append(argv)
        if errors is not None and index < len(errors):
            error = errors[index]
            if error is not None:
                raise error
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    return seen


def test_echo_resolves_through_whatever_shell_this_platform_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real ``!echo x`` resolves to ``x`` through BOTH resolvers, everywhere.

    This is a regression pin on the ``sh`` path, not a discriminator for what
    #227 adds. On the windows leg it takes candidate 2 — the image ships Git's
    ``sh.exe`` in ``C:\\Program Files\\Git\\usr\\bin`` and the leg was measured
    running a real ``sh -c`` spawn at ``7fa6796`` — so it exercises no
    PowerShell and no ``cmd`` argv; cases 16 and 17 are the ones that do.

    The shell it resolved through and the elapsed are WARNED, never asserted: a
    shell-start budget on a shared runner is a flake, and the number is here to
    be read off the leg's log rather than to gate it.
    """

    seen = _record_spawns(monkeypatch)

    started = time.monotonic()
    assert resolve_config_value_uncached("!echo x") == "x"
    assert resolve_config_value("!echo x") == "x"
    elapsed = time.monotonic() - started

    assert seen, "nothing was spawned at all"
    spawned = seen[-1]
    argv0 = spawned[0] if isinstance(spawned, list) else spawned
    warnings.warn(
        f"#227 !echo x resolved through {argv0!r}: {elapsed:.3f}s", stacklevel=1
    )


def test_win32_runs_a_posix_sh_before_powershell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sh`` outranks PowerShell in the ``!command`` chain — and only there.

    A ``!command`` was written for ``sh``. Every Windows box where one works
    today has an ``sh`` (Git-for-Windows, MSYS2, Cygwin), and this step is what
    keeps it running under that shell instead of silently handing POSIX syntax
    to PowerShell. The bash tool does NOT take this step (case
    ``test_the_bash_tool_and_command_resolution_share_one_windows_chain``): ``sh``
    is classifiable, so adding it there would flip AUTO mode's dialect on those
    boxes, which is ADR-0237/#204's decision and not this one's.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    bin_dir = tmp_path / "bin"
    sh = _on_path(bin_dir, "sh")
    _on_path(bin_dir, "pwsh")

    cmd = "printf x"
    candidates = rc._shell_argv_candidates(
        cmd, platform="win32", env={"PATH": str(bin_dir)}
    )

    first = candidates[0]
    assert isinstance(first, list)
    assert Path(first[0]) == sh
    assert first[1:] == ["-c", cmd]


def test_win32_without_sh_falls_to_powershell_then_comspec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``sh``: PowerShell, hardened, then the two ``cmd`` command lines.

    ``-NoProfile`` is correctness, not hygiene: measured on PowerShell 7, a
    profile that writes to stdout is PREPENDED to the resolved key
    (``profile-banner\\nsk-KEY``). ``-NonInteractive`` is not about timing —
    ``stdin=DEVNULL`` already ends a plain ``Read-Host`` (measured 0.652 s) —
    it is about three things stdin cannot reach: the prompt TEXT landing inside
    the key (measured: ``'give me a key: \\nGOT:'`` against ``'GOT:'``), the
    masked read (``Read-Host -AsSecureString``, ``Get-Credential``) that opens
    ``CONIN$`` directly and so costs the whole timeout, and the prompt WRITE to
    ``CONOUT$`` that redirection cannot capture. The argv is all that pins them.

    ``/d`` is ``-NoProfile``'s counterpart for ``cmd``'s registry ``AutoRun``
    and ``/s`` makes its quote stripping deterministic; both are reasoned from
    ``cmd /?``, not measured on win32.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    bin_dir = tmp_path / "bin"
    pwsh = _on_path(bin_dir, "pwsh")
    comspec = r"C:\Windows\system32\cmd.exe"

    cmd = "printf x"
    candidates = rc._shell_argv_candidates(
        cmd, platform="win32", env={"PATH": str(bin_dir), "COMSPEC": comspec}
    )

    assert len(candidates) == 3
    powershell_argv = candidates[0]
    assert isinstance(powershell_argv, list)
    assert Path(powershell_argv[0]) == pwsh
    assert powershell_argv[1:] == ["-NoProfile", "-NonInteractive", "-Command", cmd]
    assert candidates[1] == f'"{comspec}" /d /s /c "{cmd}"'
    assert candidates[2] == f'"cmd.exe" /d /s /c "{cmd}"'


def test_an_existing_shell_from_the_env_wins_on_win32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``$SHELL`` is candidate 1 — but only when it names a file that exists.

    The common way ``$SHELL`` is set on Windows is Git-Bash exporting the MSYS
    path ``/usr/bin/bash``, which :class:`subprocess.Popen` cannot spawn. That
    spelling is skipped and the chain moves on, exactly as the bash tool's own
    win32 arm has done since #104.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    bin_dir = tmp_path / "bin"
    sh = _on_path(bin_dir, "sh")
    real_shell = _executable(tmp_path / "git", "bash.exe")

    cmd = "printf x"
    chosen = rc._shell_argv_candidates(
        cmd, platform="win32", env={"SHELL": str(real_shell), "PATH": str(bin_dir)}
    )
    assert chosen[0] == [str(real_shell), "-c", cmd]
    second = chosen[1]
    assert isinstance(second, list)
    assert Path(second[0]) == sh

    # An MSYS-style path: shaped like a shell, absent from the filesystem. The
    # literal ``/usr/bin/bash`` cannot be used — it EXISTS on the gating
    # ubuntu-latest leg (usr-merge), so the chain would honour it and the case
    # would assert the opposite of what it means.
    msys_bash = str(tmp_path / "msys" / "usr" / "bin" / "bash")
    skipped = rc._shell_argv_candidates(
        cmd, platform="win32", env={"SHELL": msys_bash, "PATH": str(bin_dir)}
    )
    first = skipped[0]
    assert isinstance(first, list)
    assert Path(first[0]) == sh


def test_the_posix_candidate_list_is_exactly_sh_dash_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX is byte-identical to what this site has always spawned.

    One candidate, ``["sh", "-c", cmd]``, and no ``which`` probe at all — the
    fall-through loop exists for the platform that has more than one candidate,
    and POSIX must not start paying for a PATH search it never needed.
    """

    import aelix_ai.oauth._resolve_config as rc

    bin_dir = tmp_path / "bin"
    _on_path(bin_dir, "sh")
    probes: list[str] = []
    real_which = shutil.which

    def counting_which(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        probes.append(str(cmd))
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", counting_which)

    cmd = "printf x"
    assert rc._shell_argv_candidates(
        cmd, platform="linux", env={"PATH": str(bin_dir)}
    ) == [["sh", "-c", cmd]]
    assert probes == []


def test_a_shell_that_cannot_be_spawned_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that is not there is a verdict on that candidate only.

    The CONSTRUCTOR is what raises, so no command ran — POSIX reaps the failed
    fork inside ``Popen.__init__`` and win32's ``CreateProcess`` fails
    atomically — and falling through cannot double-run anything.
    """

    import aelix_ai.oauth._resolve_config as rc

    absent = str(tmp_path / "absent" / "sh")
    candidates: list[Any] = [
        [absent, "-c", "ignored"],
        [sys.executable, "-c", "print('sentinel')"],
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(monkeypatch)

    assert resolve_config_value_uncached("!ignored") == "sentinel"
    assert len(seen) == 2


@pytest.mark.parametrize(
    "code",
    [
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EACCES,
        errno.EPERM,
        errno.ENOEXEC,
        errno.ELOOP,
        errno.EINVAL,
    ],
)
def test_a_candidate_that_is_not_a_loadable_image_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """The discriminator for classifying by errno instead of by class.

    A ``sh.cmd`` / ``pwsh.bat`` / non-PE ``$SHELL`` on win32 is
    ``ERROR_BAD_EXE_FORMAT`` (193), which CPython's ``PC/errmap.h`` maps to
    ``ENOEXEC`` BEFORE ``OSError``'s subclass table is consulted — and that
    table has no ``ENOEXEC`` entry, so it arrives as a BARE ``OSError``. A
    ``except (FileNotFoundError, NotADirectoryError, PermissionError)`` tuple
    would abort the chain there, before the ``cmd.exe`` floor, leaving
    ``!command`` exactly as dead as #227 found it (measured against that
    spelling: result ``None`` after ONE spawn).

    The parametrization walks EVERY member of the allowlist, because each one
    is a separate way back to that dead ``!command`` and only ``ENOEXEC`` was
    pinned: dropping any of ``EINVAL``/``EACCES``/``EPERM``/``ENOTDIR``/
    ``ELOOP`` was measured leaving this file, ``test_bash_shell_win32.py`` and
    ``tests/process_tree`` at ``185 passed`` while the chain aborted at
    candidate 1. ``EINVAL`` is the one that matters most — it is where
    ``errmap.h``'s ``default:`` arm sends every winerror the table does not
    name. The list is a LITERAL and deliberately not
    ``sorted(rc._NOT_A_RUNNABLE_SHELL)``: parametrizing over the set under test
    is self-referential — deleting a member deletes its case and the suite
    stays green (measured: ``6 passed`` where the literal goes RED).
    """

    import aelix_ai.oauth._resolve_config as rc

    candidates: list[Any] = [
        [str(tmp_path / "not-an-image"), "-c", "ignored"],
        [sys.executable, "-c", "print('sentinel')"],
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(monkeypatch, errors=[OSError(code, os.strerror(code))])

    assert resolve_config_value_uncached("!ignored") == "sentinel"
    assert len(seen) == 2


def test_a_spawn_error_that_is_not_a_missing_shell_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EMFILE``/``ENOMEM``/``EBADF`` are process-resource failures.

    The next candidate cannot fix a descriptor table that is full, so the chain
    keeps today's answer after ONE spawn instead of spending more of them.
    """

    import aelix_ai.oauth._resolve_config as rc

    candidates: list[Any] = [
        [sys.executable, "-c", "print('first')"],
        [sys.executable, "-c", "print('second')"],
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(
        monkeypatch, errors=[OSError(errno.EMFILE, "Too many open files")]
    )

    assert resolve_config_value_uncached("!ignored") is None
    assert len(seen) == 1


def test_a_malformed_argv_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``ValueError`` is a verdict on the ARGV, not on the shell.

    ``Popen`` raises it for an empty argv or an embedded null byte — nothing a
    different shell would survive — so it keeps today's ``None`` after one
    spawn rather than walking the whole chain to the same answer.
    """

    import aelix_ai.oauth._resolve_config as rc

    candidates: list[Any] = [
        [sys.executable, "-c", "print('first')"],
        [sys.executable, "-c", "print('second')"],
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(monkeypatch, errors=[ValueError("embedded null byte")])

    assert resolve_config_value_uncached("!ignored") is None
    assert len(seen) == 1


def test_the_first_candidate_that_spawns_wins_and_nothing_after_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop STOPS at its first success — the positive arm of the two cases
    above, and the only thing in the suite that sees the loop's exit.

    Every other fall-through case stages a FIRST candidate that cannot spawn,
    so the loop ends by exhaustion and reaches the same answer with or without
    its exit; the two above return from inside it. Measured: with the exit
    deleted the whole suite stays green on darwin (10277 passed) while a
    three-candidate chain runs the user's CREDENTIAL command three times and
    resolves to the LAST candidate's stdout instead of the first's — on win32,
    ``cmd.exe``'s answer to a command written for ``sh``.
    """

    import aelix_ai.oauth._resolve_config as rc

    candidates: list[Any] = [
        [sys.executable, "-c", "print('first')"],
        [sys.executable, "-c", "print('second')"],
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(monkeypatch)

    assert resolve_config_value_uncached("!ignored") == "first"
    assert len(seen) == 1


def test_every_candidate_failing_to_spawn_still_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chain where nothing spawns answers what this site answers today.

    A spawn failure is a verdict on one candidate; running out of candidates is
    the same ``None`` ``main`` returns for a failed spawn, and the failure seat
    still names the FIRST candidate so a total failure has a shell to report.
    """

    import aelix_ai.oauth._resolve_config as rc

    floor = tmp_path / "c" / "cmd.exe"
    candidates: list[Any] = [
        [str(tmp_path / "a" / "sh"), "-c", "ignored"],
        [str(tmp_path / "b" / "pwsh"), "-Command", "ignored"],
        f'"{floor}" /d /s /c "ignored"',
    ]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: candidates)
    seen = _record_spawns(
        monkeypatch,
        errors=[OSError(errno.ENOENT, "No such file or directory")] * 3,
    )

    failure = rc._Failure()
    assert rc._run_shell_command("ignored", failure=failure) is None
    assert len(seen) == 3
    assert failure.argv == candidates[0]


@pytest.mark.parametrize("raw", ["sk-abc\r\n", " sk-abc \t\r\n"])
def test_a_carriage_return_never_reaches_the_resolved_value(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two resolvers trim the same way, on every platform.

    ``resolve_config_value`` trimmed with ``rstrip("\\n")`` while its sibling
    used ``.strip()``. Under a shell whose ``echo`` emits CRLF — cmd.exe and
    PowerShell both do — the cached path kept a bare carriage return INSIDE an
    ``Authorization`` header (measured: cached ``'sk-abc\\r'`` against uncached
    ``'sk-abc'``). ``rstrip("\\r\\n")`` is the partial fix and this case pins
    against it too: the second parametrization has leading and trailing spaces
    and a tab that only ``.strip()`` removes.
    """

    import aelix_ai.oauth._resolve_config as rc

    monkeypatch.setattr(rc, "_run_shell_command", lambda *a, **k: (0, raw))

    assert resolve_config_value_uncached("!ignored") == "sk-abc"
    assert resolve_config_value("!ignored") == "sk-abc"


def test_the_failure_message_names_the_shell_that_actually_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raised ``CalledProcessError`` names the argv that RAN.

    ``resolve_config_value`` rebuilt ``["sh", "-c", cmd]`` for its message; on
    win32 after #227 that is a lie, and it is the lie #227 is about — the
    failure blamed the user's command for a shell that was never there. The argv
    rides back on the existing ``_Failure`` seat.

    Two candidates, and the first one CANNOT spawn, on purpose: with a
    single-candidate list the seat set before the loop already holds the right
    answer, so the case could not see the assignment inside it being dropped
    (measured — that mutation left a one-candidate version of this case green).
    The message must name the second and neither the first nor ``sh``.
    """

    import aelix_ai.oauth._resolve_config as rc

    absent = [str(tmp_path / "absent" / "sh"), "-c", "ignored"]
    ran = [sys.executable, "-c", "raise SystemExit(3)"]
    monkeypatch.setattr(rc, "_shell_argv_candidates", lambda *a, **k: [absent, ran])

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        resolve_config_value("!ignored")

    text = str(excinfo.value)
    assert str(ran) in text
    assert str(absent) not in text
    assert str(["sh", "-c", "ignored"]) not in text


def test_a_version_suffixed_powershell_is_still_hardened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The family is read with ``shell_basename``, not with a literal name set.

    A literal ``{"pwsh", "powershell"}`` against ``Path(p).stem.lower()`` passes
    the three chain cases above and still diverges on 18 of 66 candidate paths:
    a ``$SHELL`` of ``pwsh-7.5.0.exe`` would take ``-Command`` with NO
    ``-NoProfile``, which is the measured profile banner inside the key.

    A real file rather than a PATH fixture on purpose: no ``which`` and no
    PATHEXT dependence, so both legs assert the identical string.
    """

    import aelix_ai.oauth._resolve_config as rc

    shell = _executable(tmp_path / "ps", "pwsh-7.5.0.exe")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    cmd = "printf x"
    candidates = rc._shell_argv_candidates(
        cmd, platform="win32", env={"SHELL": str(shell), "PATH": str(empty)}
    )

    assert candidates[0] == [
        str(shell),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        cmd,
    ]


def test_a_quoted_command_reaches_each_windows_shell_in_that_shell_s_own_convention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two quoting conventions, and the second is a deliberate divergence.

    The PowerShell candidate is correct as a ``list``: on win32 the .NET host
    CRT-parses the command line back into argv before PowerShell's own parser
    sees it, so ``\\"`` — what :func:`subprocess.list2cmdline` emits — is the
    escape Microsoft documents for ``-Command``. ``cmd`` implements no ``\\"``
    at all and ``cmd /?``'s rule 2 strips one leading and one trailing quote, so
    the same rendering would hand it literal backslash-quotes. Hence the raw
    command line for that family, with the shell PATH QUOTED: a spaced
    ``%COMSPEC%`` is exactly what a bare f-string breaks.

    :func:`subprocess.list2cmdline` is pure Python and platform-independent, so
    this renders — and never spawns — on every leg.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    bin_dir = tmp_path / "bin"
    pwsh = _on_path(bin_dir, "pwsh")
    comspec = r"C:\Program Files\Nope\cmd.exe"

    cmd = 'op read "op://v/k"'
    candidates = rc._shell_argv_candidates(
        cmd, platform="win32", env={"PATH": str(bin_dir), "COMSPEC": comspec}
    )
    rendered = [
        subprocess.list2cmdline(c) if isinstance(c, list) else c for c in candidates
    ]

    powershell_argv = candidates[0]
    assert isinstance(powershell_argv, list)
    assert Path(powershell_argv[0]) == pwsh
    assert rendered[0].endswith(
        '-NoProfile -NonInteractive -Command "op read \\"op://v/k\\""'
    )
    assert rendered[1] == f'"{comspec}" /d /s /c "op read "op://v/k""'
    assert rendered[2] == '"cmd.exe" /d /s /c "op read "op://v/k""'


def test_the_win32_spawn_kwargs_hide_the_console_window() -> None:
    """The win32 child gets no console window of its own.

    ``CreateProcess`` gives a console-subsystem child a NEW console when the
    parent has none, and a ``pythonw``-shaped host is a shape this repo already
    defends against. Pi passes ``windowsHide: true`` at exactly this spawn and
    Node's default is false; CPython gives it for free only for ``shell=True``,
    and this site spawns a list argv.

    ``CREATE_NO_WINDOW`` is OR'd in HERE and not inside
    ``containment_spawn_kwargs``: three of that helper's other six call sites
    (``extensions/subprocess_hooks``, ``rpc/rpc_client`` and the print channel,
    whose tree the reaper soft-kills) end their trees with ``soft_kill()`` →
    ``ctrl_break()``, which needs the shared console this site never uses, so
    the flag there would silently demote three teardowns to hard kills.

    This is the discriminator on every leg. The runner HAS a console, so no
    behavioural case could tell the flag's presence from its absence.
    """

    import aelix_ai.oauth._resolve_config as rc

    assert rc._spawn_kwargs(platform="win32") == {"creationflags": 0x0800_0200}
    assert rc._spawn_kwargs(platform="linux") == {"process_group": 0}


def test_an_injected_platform_never_reaches_the_real_spawn_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_spawn_kwargs()`` takes NO argument, and this is what says so.

    ``platform`` steers WHICH candidates are built, never HOW they are spawned:
    on POSIX ``Popen(creationflags=…)`` raises ``ValueError``, which this
    function turns into ``None``. Measured — with ``**_spawn_kwargs(platform)``
    at the spawn the node set (oauth + the win32 shell file + process_tree, 183
    cases) stays green while this call answers ``None`` instead of ``(0, "x")``.

    ``$SHELL`` is :data:`sys.executable` because it is the one path that exists
    and spawns on EVERY leg, so both arms assert the identical value.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    outcome = rc._run_shell_command(
        "print('x')",
        platform="win32",
        env={"SHELL": sys.executable, "PATH": str(empty)},
    )
    assert outcome is not None
    assert outcome[0] == 0
    assert outcome[1].strip() == "x"


def test_a_forced_powershell_candidate_really_runs_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the windows leg a PowerShell argv is really spawned and read back.

    The leg ships Git's ``sh.exe``, so every other case there takes candidate 2
    and no test would ever execute the argv this issue adds. Narrowing ``PATH``
    to PowerShell's own directory, with no ``$SHELL``, forces candidate 1 to be
    PowerShell and the value comes back through the real hardened invocation —
    byte-for-byte ``x``, so neither a profile banner nor PowerShell's CRLF
    survives into the key.

    POSIX takes the same call with its PATH untouched: ``env`` is ignored off
    win32, the single ``sh -c`` candidate runs, and the identical assertion
    holds. Narrowing PATH there would only hide ``sh`` from ``Popen`` itself.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.chdir(empty)
    if sys.platform == "win32":
        found = shutil.which("pwsh") or shutil.which("powershell")
        home = (
            str(Path(found).parent)
            if found
            else os.path.join(
                os.environ.get("SYSTEMROOT", r"C:\Windows"),
                "System32",
                "WindowsPowerShell",
                "v1.0",
            )
        )
        monkeypatch.setenv("PATH", home)

    seen = _record_spawns(monkeypatch)
    started = time.monotonic()
    assert resolve_config_value_uncached("!echo x") == "x"
    assert resolve_config_value("!echo x") == "x"
    elapsed = time.monotonic() - started

    assert seen, "nothing was spawned at all"
    spawned = seen[-1]
    assert spawned in rc._shell_argv_candidates("echo x")
    if sys.platform == "win32":
        # The value alone cannot name the shell: ``echo x`` answers ``x`` under
        # ``sh``, PowerShell 5.1/7 and ``cmd`` alike, and the membership check
        # above admits EVERY candidate the chain built — including the two
        # ``cmd`` command lines that ``%COMSPEC%`` and the floor always append,
        # which this case does not clear the way it clears ``$SHELL``. Without
        # this, a runner image with no PowerShell would resolve the floor, the
        # case would stay green, and ADR-0238 would keep claiming a PowerShell
        # argv was forced. Named by FAMILY, because WHICH PowerShell the image
        # ships is not this case's claim; the hardening flags are.
        assert isinstance(spawned, list)
        assert shell_basename(spawned[0]) in POWERSHELL_NAMES
        assert spawned[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    argv0 = spawned[0] if isinstance(spawned, list) else spawned
    warnings.warn(
        f"#227 forced-PowerShell candidate ran {argv0!r}: {elapsed:.3f}s",
        stacklevel=1,
    )


def test_the_cmd_floor_really_runs_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the windows leg the ``cmd.exe`` floor is really spawned too.

    An empty ``PATH`` and no ``%COMSPEC%`` leave the floor as the only
    candidate, so the leg executes the raw command line — ``/d`` against
    ``AutoRun``, ``/s`` for deterministic quote stripping — and reads the value
    back through ``cmd``'s own CRLF. ``cmd.exe`` still resolves: ``CreateProcess``
    searches the system directory whatever ``PATH`` says.

    POSIX takes the same call with its PATH untouched, for the reason the
    PowerShell case above gives.
    """

    import aelix_ai.oauth._resolve_config as rc

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.chdir(empty)
    if sys.platform == "win32":
        monkeypatch.delenv("COMSPEC", raising=False)
        monkeypatch.setenv("PATH", str(empty))

    seen = _record_spawns(monkeypatch)
    started = time.monotonic()
    assert resolve_config_value_uncached("!echo x") == "x"
    assert resolve_config_value("!echo x") == "x"
    elapsed = time.monotonic() - started

    assert seen, "nothing was spawned at all"
    spawned = seen[-1]
    assert spawned in rc._shell_argv_candidates("echo x")
    if sys.platform == "win32":
        assert spawned == '"cmd.exe" /d /s /c "echo x"'
    warnings.warn(f"#227 cmd floor ran {spawned!r}: {elapsed:.3f}s", stacklevel=1)
