"""Sprint 6e W6 (P-141) — ``resolve_config_value`` helper tests.

Pi parity: ``coding-agent/core/resolve-config-value.ts`` (SHA 734e08e).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time
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

    ``setsid`` would drop the controlling terminal, and this is the site where
    credential helpers run: ``gpg``/``pass``/pinentry open ``/dev/tty`` and
    answer ``sh: /dev/tty: Device not configured`` without one (measured under a
    real pty during the #202 review). A new group in the SAME session keeps the
    terminal and is reached by ``killpg`` identically. The tty consequence is not
    reproducible headless — the CI runner has no controlling terminal to lose —
    so the MECHANISM is what is pinned here.
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
