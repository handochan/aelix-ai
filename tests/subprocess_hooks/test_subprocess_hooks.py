"""Sprint 6h₉e — subprocess hook dispatch tests (Tier 4b, ADR-0102).

Covers the 25 scenarios enumerated in the Sprint 6h₉e spec §7:

- ``run_hook_subprocess`` spawn core (1-6).
- ``serialize_hook_event`` stdin envelope (7-9).
- ``parse_hook_output`` stdout / exit-code mapping (10-17).
- ``validate_subprocess_hook_event`` + module invariant (18-21).
- Loader wiring with a real ``aelix-plugin.toml`` (22-24).
- End-to-end: subprocess deny composes with the in-process reducer (25).

``asyncio_mode = "auto"`` — plain ``async def test_*``, no decorator.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import aelix_coding_agent.extensions.subprocess_hooks as subprocess_hooks
import pytest
from aelix_agent_core.contracts import HookContrib
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    BashToolCallHookEvent,
    HookBus,
    InputHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.extensions.loader import (
    ExtensionManifestError,
    discover_and_load_extensions,
)
from aelix_coding_agent.extensions.subprocess_hooks import (
    SUBPROCESS_HOOK_EVENTS,
    HookSubprocessOutcome,
    make_subprocess_handler,
    parse_hook_output,
    run_hook_subprocess,
    serialize_hook_event,
    validate_subprocess_hook_event,
)

from tests.process_probe import (
    STATE_ALIVE,
    STATE_GONE,
    STATE_ZOMBIE,
    await_dead_or_zombie,
    is_dead_or_zombie,
    probe_state,
)

# === Helpers ===


def _make_ctx(cwd: str = "/tmp/work") -> ExtensionContext:
    return ExtensionContext(
        _ExtensionRuntime(),
        cwd=cwd,
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


def _outcome(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> HookSubprocessOutcome:
    return HookSubprocessOutcome(
        exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=timed_out
    )


def _py_command(tmp_path: Path, body: str, *, name: str = "hook_child.py") -> str:
    """Write ``body`` to a temp .py file and return a shell command that runs it.

    ``run_hook_subprocess`` takes a shell command *string* and dispatches it
    through ``create_subprocess_shell`` — cmd.exe on Windows, which neither
    tokenises single quotes nor expands ``$VAR``. A script path invoked with
    ``sys.executable`` carries no shell syntax at all, so the same command
    string runs on every platform. Both paths are double-quoted because
    ``tmp_path`` and the interpreter path may contain spaces.
    """

    script = tmp_path / name
    script.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _reap(pid: int) -> None:
    """Kill ``pid`` if it is still there. Cleanup only — never an assertion.

    Deliberately root-only: cleanup for a containment test must not reach for a
    process GROUP, which is the thing under test.

    On win32 ``taskkill.exe`` is resolved from ``%SystemRoot%\\System32`` with
    the bare name only as the retry, exactly as the product's ``_taskkill_tree``
    does: ``System32`` is not always on ``PATH`` and a bare name then fails
    ``ENOENT`` (Pi #6596/#8560, fixed there in ``7af2d27d``). A silent no-op is
    worst here, because this helper is the only thing standing between a failed
    assertion and a 60 s sleeper leaking into the rest of the leg (win-leg/F8).
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


#: A hook that spawns a grandchild, records its pid, then outlives any timeout.
#:
#: The grandchild takes NO containment kwargs, so it stays in the hook shell's
#: group (POSIX) / job (win32) — the shape ``sh -c "sleep 6 | cat"`` had on
#: ``main``, where teardown signalled the shell and the pipeline ran on.
#: ``os.replace`` because the reader is another process: a half-written pid file
#: would be read as a different pid.
_ORPHANED_GRANDCHILD_SOURCE = """\
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
path = __PID_FILE__
with open(path + ".part", "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
os.replace(path + ".part", path)
time.sleep(60)
"""

#: A hook that backgrounds a helper and returns 0 — the success path.
#:
#: ``new_session=True`` so the helper is not merely detached from our pipes but
#: from our group too: this case is about ``close()`` being a release, and a
#: helper that shared the group would leave the question open on POSIX.
_BACKGROUNDED_HELPER_SOURCE = """\
import os
import subprocess
import sys

from aelix_ai.utils._process_tree import containment_spawn_kwargs

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    **containment_spawn_kwargs(new_session=True),
)
path = __PID_FILE__
with open(path + ".part", "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
os.replace(path + ".part", path)
"""


#: A hook whose ROOT survives the soft signal, so the teardown's 1.0 s grace is
#: a real window a cancellation can land inside (review posix2/F6).
#:
#: One shape for both legs and no ``/bin/sh``: the POSIX idiom
#: ``sh -c "trap '' TERM; …"`` has no win32 counterpart, so the child installs
#: Python handlers for whichever of ``SIGTERM`` / ``SIGBREAK`` exists and simply
#: keeps running — the same "delivered AND survived" stub shape #207 uses. It
#: records its OWN pid as well as the grandchild's: after a cancellation there
#: is no outcome object to read the root's fate from.
_SIGNAL_SURVIVING_SOURCE = """\
import os
import signal
import subprocess
import sys
import time


def _keep_running(*_args):
    return


for _name in ("SIGTERM", "SIGBREAK"):
    _sig = getattr(signal, _name, None)
    if _sig is not None:
        signal.signal(_sig, _keep_running)

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
path = __PID_FILE__
with open(path + ".part", "w", encoding="utf-8") as handle:
    handle.write("%d\\n%d" % (os.getpid(), child.pid))
os.replace(path + ".part", path)
while True:
    time.sleep(0.05)
"""


def _pid_file_source(source: str, pid_file: Path) -> str:
    """Bake ``pid_file`` into ``source`` as a literal.

    ``repr`` and not an f-string: a Windows ``tmp_path`` is full of backslashes
    and only a repr escapes them into a literal the child can actually parse.
    """

    return source.replace("__PID_FILE__", repr(str(pid_file)))


async def _await_pid_file(pid_file: Path, *, timeout: float = 15.0) -> list[int]:
    """Wait for the hook's pid file and return the pids it holds, in order.

    The cancellation cases have to cancel a hook that has ALREADY spawned what
    they are about to assert on, so the wait is the vacuity guard: without it a
    cancel that lands before the grandchild exists would measure nothing and
    still pass. The file is written through ``os.replace``, so a non-empty read
    is a complete read.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists():
            text = pid_file.read_text(encoding="utf-8").strip()
            if text:
                return [int(line) for line in text.splitlines()]
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"{pid_file} never appeared within {timeout}s — the hook never got far "
        "enough to spawn anything, so this case would measure nothing"
    )


def _write_hook_plugin(
    parent: Path,
    *,
    name: str,
    event: str,
    command: str,
    shell_exec: bool,
) -> None:
    """Write a hooks-only ``aelix-plugin.toml`` plugin under .aelix/extensions."""

    pkg_dir = parent / ".aelix" / "extensions" / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    caps = "\nshell_exec = true" if shell_exec else ""
    manifest = textwrap.dedent(
        f"""
        [plugin]
        id = "{name}"
        name = "Hook Plugin {name}"
        version = "0.1.0"
        description = "Subprocess hook test plugin"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/{name}"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [capabilities]{caps}

        [activation]
        on_startup_finished = true

        [[contributes.hooks]]
        event = "{event}"
        command = {command!r}
        timeout_ms = 2000
        """
    ).strip()
    (pkg_dir / "aelix-plugin.toml").write_text(manifest, encoding="utf-8")


# === run_hook_subprocess (1-6) ===


async def test_run_subprocess_echo_stdin_to_stdout() -> None:
    """#1 — ``cat`` echoes stdin → stdout; exit 0."""

    payload = '{"hook_event_name": "tool_call"}'
    outcome = await run_hook_subprocess("cat", payload, timeout_ms=2000)
    assert outcome.exit_code == 0
    assert outcome.stdout == payload
    assert outcome.timed_out is False


async def test_run_subprocess_exit0_with_json_stdout(tmp_path: Path) -> None:
    """#2 — exit 0 with JSON on stdout."""

    cmd = _py_command(tmp_path, 'print(\'{"decision": "block"}\')')
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=2000)
    assert outcome.exit_code == 0
    assert '"decision"' in outcome.stdout


async def test_run_subprocess_exit2_with_stderr(tmp_path: Path) -> None:
    """#3 — exit 2 with stderr captured; not timed out."""

    cmd = _py_command(tmp_path, 'import sys; sys.stderr.write("nope"); sys.exit(2)')
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=2000)
    assert outcome.exit_code == 2
    assert "nope" in outcome.stderr
    assert outcome.timed_out is False


async def test_run_subprocess_timeout() -> None:
    """#4 — ``sleep 5`` with 200ms timeout → timed_out, exit 124, fast."""

    import time

    start = time.monotonic()
    outcome = await run_hook_subprocess("sleep 5", "", timeout_ms=200)
    elapsed = time.monotonic() - start
    assert outcome.timed_out is True
    assert outcome.exit_code == 124
    assert elapsed < 2.0


async def test_run_subprocess_nonexistent_command_no_raise() -> None:
    """#5 — nonexistent command via shell → non-zero exit, no raise."""

    outcome = await run_hook_subprocess(
        "this_cmd_does_not_exist_xyz", "", timeout_ms=2000
    )
    assert outcome.exit_code != 0
    assert outcome.timed_out is False


async def test_run_subprocess_stdout_cap(tmp_path: Path) -> None:
    """#6 — stdout capped at 10k chars."""

    cmd = _py_command(tmp_path, 'import sys; sys.stdout.write("x" * 20000)')
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=5000)
    assert outcome.exit_code == 0
    assert len(outcome.stdout) == 10_000


async def test_a_timed_out_hook_does_not_orphan_what_it_spawned(tmp_path: Path) -> None:
    """#202 — the timeout teardown reaches the hook's descendants, not just the shell.

    Fails on ``main``: there the teardown was ``proc.terminate()`` /
    ``proc.kill()``, which signals the shell alone, so the grandchild kept
    running (on POSIX whenever the shell forked; on Windows every time, because
    ``TerminateProcess`` on ``cmd.exe`` orphans what it launched).

    Liveness is ``tests.process_probe`` and never ``os.kill(pid, 0)`` — signal 0
    on Windows terminates the target (#203).
    """

    pid_file = tmp_path / "grandchild.pid"
    cmd = _py_command(tmp_path, _pid_file_source(_ORPHANED_GRANDCHILD_SOURCE, pid_file))

    # The timeout has to clear the hook's SETUP — shell, interpreter startup,
    # the grandchild spawn, the pid file — or the case fails on a slow runner
    # for a reason that has nothing to do with containment. 1500 ms is ~75x the
    # 16-21 ms measured on darwin, sized for two interpreter startups behind a
    # cmd.exe on the windows leg, which is the leg this case exists for. Any
    # bound under the child's 60 s sleep exercises the same teardown.
    #
    # Run as a TASK so the grandchild can be observed ALIVE before the teardown
    # reaches it. #203's lesson, and the sibling in tests/process_tree does the
    # same (`_spawn_parent_of_a_grandchild`): a grandchild that had died on its
    # own — a bad interpreter path, an exhausted process table — would satisfy
    # the death assertion below while measuring nothing at all (review
    # posix2/F9). The probe cannot come after the await: by then the teardown
    # has already run, which is the whole point of the case.
    task = asyncio.ensure_future(run_hook_subprocess(cmd, "", timeout_ms=1500))
    (grandchild,) = await _await_pid_file(pid_file)
    assert probe_state(grandchild) == STATE_ALIVE, (
        f"grandchild {grandchild} was never running — this case would pass for free"
    )
    outcome = await task

    assert outcome.timed_out is True
    assert outcome.exit_code == 124
    try:
        state = await await_dead_or_zombie(grandchild, timeout=5.0)
        assert state in (STATE_GONE, STATE_ZOMBIE), (
            f"grandchild {grandchild} outlived the hook teardown: {state}"
        )
    finally:
        _reap(grandchild)


async def test_a_hook_that_backgrounds_a_helper_keeps_it_after_a_normal_return(
    tmp_path: Path,
) -> None:
    """#202 — the SUCCESS path takes nothing away. ``close()`` is a release.

    Revision 1 of the spec had ``close()`` send ``killpg(SIGKILL)``, which killed
    a helper the hook had deliberately backgrounded and exited 0 over — measured,
    and the reason this site attaches with ``kill_on_close=False``. On win32 the
    helper IS in the job (membership is inherited regardless), so what this pins
    there is that ``CloseHandle`` without ``KILL_ON_JOB_CLOSE`` does not end it.
    """

    pid_file = tmp_path / "helper.pid"
    cmd = _py_command(tmp_path, _pid_file_source(_BACKGROUNDED_HELPER_SOURCE, pid_file))

    outcome = await run_hook_subprocess(cmd, "", timeout_ms=10_000)

    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    helper = int(pid_file.read_text(encoding="utf-8").strip())
    try:
        assert probe_state(helper) == STATE_ALIVE, "the hook's backgrounded helper was killed"
    finally:
        _reap(helper)


async def test_a_cancelled_hook_does_not_leave_its_tree_behind(tmp_path: Path) -> None:
    """#202 — a cancel during ``communicate()`` ends the tree before it propagates.

    The branch was unpinned: deleting ``except asyncio.CancelledError:
    tree.hard_kill()`` from ``run_hook_subprocess`` left the whole suite green
    (mutation MUT-4, measured). It is production-reachable — the only caller,
    ``make_subprocess_handler``'s ``_handler``, guards with ``except Exception``
    and :class:`asyncio.CancelledError` is a ``BaseException``, so this branch
    is the only thing that ends the tree when a turn is aborted.
    """

    pid_file = tmp_path / "grandchild.pid"
    cmd = _py_command(tmp_path, _pid_file_source(_ORPHANED_GRANDCHILD_SOURCE, pid_file))

    # The timeout is far out of reach: the subject is the cancellation, not the
    # timeout ladder.
    task = asyncio.ensure_future(run_hook_subprocess(cmd, "", timeout_ms=30_000))
    (grandchild,) = await _await_pid_file(pid_file)
    try:
        assert probe_state(grandchild) == STATE_ALIVE, (
            f"grandchild {grandchild} was never running"
        )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        state = await await_dead_or_zombie(grandchild, timeout=5.0)
        assert state in (STATE_GONE, STATE_ZOMBIE), (
            f"grandchild {grandchild} outlived the cancellation: {state}"
        )
    finally:
        _reap(grandchild)


async def test_a_hook_cancelled_inside_the_timeout_teardown_still_loses_its_tree(
    tmp_path: Path,
) -> None:
    """#202 — the OTHER cancellation window: inside the teardown's own waits.

    Measured on ``main`` of this branch (review posix2/F6): with the
    ``CancelledError`` handler nested beside the timeout teardown rather than
    around it, a cancel that arrived during the 1.0 s soft grace escaped it
    entirely — ``finally`` only calls ``close()``, which signals nothing on
    POSIX — and the whole hook tree survived. The root here SURVIVES the soft
    signal on purpose, which is what makes the grace a window at all.

    The cancel is timed to land inside that grace. If a slow runner makes the
    hook's setup outlast the timeout, the cancel lands wherever it lands and the
    case still asserts the thing that matters: nothing of the tree is left.
    """

    pid_file = tmp_path / "tree.pid"
    cmd = _py_command(tmp_path, _pid_file_source(_SIGNAL_SURVIVING_SOURCE, pid_file))

    started = time.monotonic()
    task = asyncio.ensure_future(run_hook_subprocess(cmd, "", timeout_ms=1500))
    root, grandchild = await _await_pid_file(pid_file)
    try:
        assert probe_state(root) == STATE_ALIVE, f"the hook root {root} was never running"
        assert probe_state(grandchild) == STATE_ALIVE, (
            f"grandchild {grandchild} was never running"
        )

        # 1.5 s timeout + ~0.3 s: the soft signal has gone out and the teardown
        # is parked in its ``wait_for(proc.wait(), timeout=1.0)``.
        await asyncio.sleep(max(0.0, 1.8 - (time.monotonic() - started)))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        for label, pid in (("the hook root", root), ("its grandchild", grandchild)):
            state = await await_dead_or_zombie(pid, timeout=5.0)
            assert state in (STATE_GONE, STATE_ZOMBIE), (
                f"{label} ({pid}) outlived a cancellation inside the teardown: {state}"
            )
    finally:
        _reap(grandchild)
        _reap(root)


async def test_a_soft_signal_that_could_not_be_sent_skips_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """win-leg/F2 — ``soft_kill() -> False`` means there is nothing to wait for.

    On a windows runner with no attached console ``GenerateConsoleCtrlEvent``
    fails and nothing reaches the tree at all. Before ``soft_kill`` reported
    that, an undeliverable console event was indistinguishable from a child
    ignoring the signal, and every win32 teardown paid its full 1.0 s grace for
    a signal that was never sent. The condition cannot be produced on POSIX
    without injection, so the seam is injected — the doctrine here is to drive
    the arm, never to skip the leg.
    """

    calls: list[str] = []
    real_attach = subprocess_hooks.ProcessTree.attach

    class _UndeliverableSoftKill:
        @staticmethod
        def attach(pid: int, **kwargs: Any) -> Any:
            tree = real_attach(pid, **kwargs)
            real_hard_kill = tree.hard_kill

            def _soft_kill(*, whole_group: bool = False) -> bool:
                calls.append("soft")
                return False

            def _hard_kill() -> None:
                calls.append("hard")
                real_hard_kill()

            tree.soft_kill = _soft_kill  # pyright: ignore[reportAttributeAccessIssue]
            tree.hard_kill = _hard_kill  # pyright: ignore[reportAttributeAccessIssue]
            return tree

    monkeypatch.setattr(subprocess_hooks, "ProcessTree", _UndeliverableSoftKill)

    cmd = _py_command(tmp_path, "import time; time.sleep(60)")
    started = time.monotonic()
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=300)
    elapsed = time.monotonic() - started

    assert outcome.timed_out is True
    assert calls == ["soft", "hard"], (
        f"the teardown did not escalate straight past the grace: {calls}"
    )
    assert elapsed < 1.0, (
        f"the teardown paid {elapsed:.2f}s for a signal that was never sent — "
        "the 1.0 s grace was not skipped"
    )


async def test_a_hook_shell_gets_a_new_group_in_the_same_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``process_group=0``, never ``start_new_session=True`` (mutation MUT-7).

    The identical decision at the ``!command`` site has had a spy test since
    #202 landed; this site had none, so swapping it to
    ``containment_spawn_kwargs(new_session=True)`` left the whole suite green on
    both legs. ``setsid`` drops the controlling terminal a hook may want; a new
    group in the SAME session keeps it and ``killpg`` reaches it identically.
    """

    seen: list[dict[str, Any]] = []
    real_create = asyncio.create_subprocess_shell

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(dict(kwargs))
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", spy)
    outcome = await run_hook_subprocess(_py_command(tmp_path, "pass"), "", timeout_ms=10_000)

    assert outcome.exit_code == 0
    assert len(seen) == 1
    kwargs = seen[0]
    if sys.platform == "win32":
        # ``CREATE_NEW_PROCESS_GROUP``, spelled as the literal the product code
        # carries because the name does not exist off Windows at runtime.
        assert kwargs["creationflags"] & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        assert kwargs["process_group"] == 0
        assert "start_new_session" not in kwargs


async def test_the_hook_tree_is_not_attached_with_kill_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``kill_on_close`` is a per-SITE decision and nothing pinned the site (MUT-6).

    Flipping this call to ``kill_on_close=True`` left the whole suite green on
    darwin; only the windows leg would have caught it, through
    ``test_a_hook_that_backgrounds_a_helper_keeps_it_after_a_normal_return``.
    A hook is allowed to background a helper and exit 0, and ``close()`` must
    not take that away on any platform.
    """

    seen: list[dict[str, Any]] = []
    real_attach = subprocess_hooks.ProcessTree.attach

    class _AttachSpy:
        @staticmethod
        def attach(pid: int, **kwargs: Any) -> Any:
            seen.append(dict(kwargs))
            return real_attach(pid, **kwargs)

    monkeypatch.setattr(subprocess_hooks, "ProcessTree", _AttachSpy)
    outcome = await run_hook_subprocess(_py_command(tmp_path, "pass"), "", timeout_ms=10_000)

    assert outcome.exit_code == 0
    assert len(seen) == 1
    assert seen[0].get("kill_on_close") is not True


# === serialize_hook_event (7-9) ===


async def test_serialize_tool_call_envelope() -> None:
    """#7 — tool_call envelope: snake_case common + tool keys."""

    ctx = _make_ctx(cwd="/proj")
    event = ToolCallHookEvent(
        tool_call_id="tc-1", tool_name="bash", args={"command": "ls"}
    )
    payload = serialize_hook_event(event, ctx)
    assert payload["hook_event_name"] == "tool_call"
    assert payload["tool_name"] == "bash"
    assert payload["tool_use_id"] == "tc-1"
    assert payload["tool_input"] == {"command": "ls"}
    assert payload["cwd"] == "/proj"
    assert payload["session_id"] == ""


async def test_serialize_typed_subclass_routes_through_tool_call() -> None:
    """#8 — ``BashToolCallHookEvent`` routes through the tool_call branch."""

    ctx = _make_ctx()
    event = BashToolCallHookEvent(tool_call_id="tc-2", args={"command": "pwd"})
    payload = serialize_hook_event(event, ctx)
    assert payload["hook_event_name"] == "tool_call"
    assert payload["tool_name"] == "bash"
    assert payload["tool_input"] == {"command": "pwd"}


async def test_serialize_input_event_and_non_serializable_arg() -> None:
    """#9 — input → prompt/source; non-serializable arg degrades via default=str."""

    import json

    ctx = _make_ctx()
    event = InputHookEvent(text="hello", source="interactive")
    payload = serialize_hook_event(event, ctx)
    assert payload["prompt"] == "hello"
    assert payload["source"] == "interactive"

    # A non-JSON-serializable arg value degrades to its str() rather than raising.
    class _Unserializable:
        def __str__(self) -> str:
            return "<obj>"

    tc = ToolCallHookEvent(
        tool_call_id="tc-3", tool_name="custom", args={"x": _Unserializable()}
    )
    tc_payload = serialize_hook_event(tc, ctx)
    encoded = json.dumps(tc_payload, default=str)
    assert "<obj>" in encoded


# === parse_hook_output (10-17) ===


async def test_parse_timeout_returns_none() -> None:
    """#10 — timeout outcome → None (fail-open)."""

    assert parse_hook_output("tool_call", _outcome(timed_out=True, exit_code=124)) is None


async def test_parse_exit2_tool_call_blocks_with_stderr() -> None:
    """#11 — exit 2 + tool_call → ToolCallResult(block=True, reason=stderr)."""

    result = parse_hook_output("tool_call", _outcome(exit_code=2, stderr="  denied  "))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "denied"


async def test_parse_exit2_non_tool_call_returns_none() -> None:
    """#12 — exit 2 on a non-tool_call event → None (not actionable in v1)."""

    assert parse_hook_output("tool_result", _outcome(exit_code=2, stderr="x")) is None


async def test_parse_exit0_permission_deny_blocks() -> None:
    """#13 — permissionDecision deny + tool_call → block with reason."""

    stdout = (
        '{"hookSpecificOutput": {"permissionDecision": "deny", '
        '"permissionDecisionReason": "nope"}}'
    )
    result = parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "nope"


async def test_parse_exit0_decision_block() -> None:
    """#14 — top-level decision:block + tool_call → block with reason."""

    stdout = '{"decision": "block", "reason": "x"}'
    result = parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "x"


async def test_parse_exit0_permission_allow_observational() -> None:
    """#15 — permissionDecision allow → None (observational in v1)."""

    stdout = '{"hookSpecificOutput": {"permissionDecision": "allow"}}'
    assert parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout)) is None


async def test_parse_exit0_empty_invalid_and_nondict_json() -> None:
    """#16 — empty stdout / invalid JSON / non-dict JSON all → None."""

    assert parse_hook_output("tool_call", _outcome(exit_code=0, stdout="")) is None
    assert (
        parse_hook_output("tool_call", _outcome(exit_code=0, stdout="{not json"))
        is None
    )
    assert (
        parse_hook_output("tool_call", _outcome(exit_code=0, stdout="true")) is None
    )


async def test_parse_exit1_non_blocking_returns_none() -> None:
    """#17 — exit 1 (non-blocking error) → None (fail-open)."""

    assert parse_hook_output("tool_call", _outcome(exit_code=1, stderr="boom")) is None


# === validate_subprocess_hook_event (18-21) ===


async def test_validate_valid_event_no_raise() -> None:
    """#18 — a valid allowlisted event does not raise."""

    validate_subprocess_hook_event("tool_call")


async def test_validate_unknown_event_raises() -> None:
    """#19 — unknown event → ExtensionManifestError."""

    try:
        validate_subprocess_hook_event("nope")
    except ExtensionManifestError as exc:
        assert "unknown hook event" in str(exc)
    else:
        raise AssertionError("expected ExtensionManifestError")


async def test_validate_known_but_not_allowlisted_raises() -> None:
    """#20 — known-but-not-allowlisted (message_update) → ExtensionManifestError."""

    try:
        validate_subprocess_hook_event("message_update")
    except ExtensionManifestError as exc:
        assert "message_update" in str(exc)
        assert "subprocess" in str(exc).lower()
    else:
        raise AssertionError("expected ExtensionManifestError")


async def test_allowlist_is_subset_of_hook_result_types() -> None:
    """#21 — module invariant: SUBPROCESS_HOOK_EVENTS <= HOOK_RESULT_TYPES."""

    assert set(HOOK_RESULT_TYPES) >= SUBPROCESS_HOOK_EVENTS


# === Loader wiring (22-24) ===


async def test_loader_hooks_only_plugin_loads(tmp_path: Path) -> None:
    """#22 — hooks-only plugin + shell_exec=true → loads, handler registered."""

    _write_hook_plugin(
        tmp_path,
        name="hookplug",
        event="tool_call",
        command="cat",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert result.errors == []
    assert len(result.extensions) == 1
    ext = result.extensions[0]
    assert ext.manifest is not None
    assert "tool_call" in ext.handlers
    assert len(ext.handlers["tool_call"]) == 1


async def test_loader_hooks_without_shell_exec_errors(tmp_path: Path) -> None:
    """#23 — hooks declared but shell_exec=false → ExtensionLoadError re shell_exec."""

    _write_hook_plugin(
        tmp_path,
        name="noshellplug",
        event="tool_call",
        command="cat",
        shell_exec=False,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert len(result.extensions) == 0
    assert len(result.errors) == 1
    assert "shell_exec" in result.errors[0].error


async def test_loader_unknown_hook_event_errors(tmp_path: Path) -> None:
    """#24 — hook on a non-allowlisted event → load error mentioning the event."""

    _write_hook_plugin(
        tmp_path,
        name="badeventplug",
        event="message_update",
        command="cat",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert len(result.extensions) == 0
    assert len(result.errors) == 1
    assert "message_update" in result.errors[0].error


# === End-to-end integration (25) ===


async def test_e2e_subprocess_deny_composes_with_reducer(tmp_path: Path) -> None:
    """#25 — a tool_call hook that denies blocks the in-process reducer.

    Loads a hooks-only plugin whose ``tool_call`` command prints a deny control
    JSON and exits 0. Builds a :class:`HookBus` from the loaded extension's
    handlers (mirror ``test_hooks.py`` wiring), emits a
    :class:`ToolCallHookEvent`, and asserts the reduced result is
    ``ToolCallResult(block=True)`` with the reason — proving the subprocess
    lane composes with the in-process reducer.
    """

    # Write the deny control JSON to a file and ``cat`` it — avoids nested
    # quoting that would break the TOML ``command`` string literal.
    deny_json = (
        '{"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"blocked"}}'
    )
    deny_file = tmp_path / "deny.json"
    deny_file.write_text(deny_json, encoding="utf-8")
    _write_hook_plugin(
        tmp_path,
        name="denyplug",
        event="tool_call",
        command=f"cat {deny_file}",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert result.errors == []
    assert len(result.extensions) == 1
    ext = result.extensions[0]

    runtime = result.runtime
    ctx = ExtensionContext(
        runtime,
        cwd=str(tmp_path),
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    bus = HookBus(ctx_factory=lambda: ctx)
    for handler in ext.handlers.get("tool_call", []):
        bus.on("tool_call", handler, error_mode="continue")

    reduced = await bus.emit(
        ToolCallHookEvent(tool_call_id="tc-1", tool_name="bash", args={"command": "rm"})
    )
    assert isinstance(reduced, ToolCallResult)
    assert reduced.block is True
    assert reduced.reason == "blocked"


# === Additional coverage (26-27) ===


async def test_run_subprocess_aelix_project_dir_injected(tmp_path: Path) -> None:
    """#26 — AELIX_PROJECT_DIR is set in the child env to the cwd value."""

    cmd = _py_command(
        tmp_path,
        """
        import os
        import sys

        sys.stdout.write(os.environ["AELIX_PROJECT_DIR"])
        """,
    )
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=2000, cwd=str(tmp_path))
    assert outcome.exit_code == 0
    assert str(tmp_path) in outcome.stdout


async def test_make_subprocess_handler_fail_open_on_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#27 — handler returns None (never raises) when run_hook_subprocess raises."""

    async def _boom(*args: object, **kwargs: object) -> HookSubprocessOutcome:
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess_hooks, "run_hook_subprocess", _boom)

    contrib = HookContrib(event="tool_call", command="x", timeout_ms=1000)
    handler = make_subprocess_handler(contrib)

    event = ToolCallHookEvent(tool_call_id="tc-99", tool_name="bash", args={})
    ctx = _make_ctx(cwd="/tmp")
    result = await handler(event, ctx)
    assert result is None
