"""Sprint 6e W6 (P-141) — ``resolve_config_value`` helper tests.

Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth._resolve_config import (
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)

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
    if sys.platform == "win32":
        # ``CREATE_NEW_PROCESS_GROUP``, spelled as the literal the product code
        # carries because the name does not exist off Windows at runtime.
        assert kwargs["creationflags"] & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
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
