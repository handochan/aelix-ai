"""Issue #5 (Lane C) — extension runtime introspection + Project Trust surface.

Part (1): ``exec`` / ``get_all_tools`` / ``get_commands`` return correct data
through a REAL :class:`ExtensionAPI` bound to a live :class:`AgentHarness`
(get_all_tools/get_commands delegate to the harness action table; exec is the
pi-faithful in-process ``execCommand`` port — pi does not bind ``exec`` through
``bindCore``).

Part (2): ``ctx.is_project_trusted()`` reflects the harness trust state; the
``project_trust`` decide/defer event fires via
:func:`emit_project_trust_event`; ``resolve_project_trusted`` honours the event
result and the ``default_project_trust`` setting (pi parity, SHA ``927e980``).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    ProjectTrustContext,
    ProjectTrustEventDecision,
    ProjectTrustEventResult,
    ProjectTrustHookEvent,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.streaming import Model
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.utils._process_tree import (
    EXIT_DRAIN_SECONDS,
    KILL_DRAIN_SECONDS,
    REAP_GRACE_SECONDS,
    AbortHandle,
)
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustStore,
    emit_project_trust_event,
    resolve_project_trusted,
)
from aelix_coding_agent.extensions import api as api_mod
from aelix_coding_agent.extensions.api import (
    ExecResult,
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)

from tests.process_probe import STATE_ALIVE, probe_state
from tests.process_tree.test_process_tree_real_processes import DEADLINE, _await_dead
from tests.process_tree.test_process_tree_real_processes import (
    strays as _strays_fixture,
)
from tests.process_tree.test_run_contained import _run_bounded
from tests.process_tree.test_run_contained_real_processes import (
    MARK,
    OUTLIVE,
    ROOT_SOURCE,
    ROOT_WITH_A_LATE_TAIL,
    _registrar,
    _with_planted_stdin,
)

#: The process-tree suite's cleanup fixture, RE-EXPORTED rather than re-written
#: — one definition of "pids this case is responsible for", one root-only reaper
#: (a cleanup that reached for a process GROUP would be reaching for the very
#: mechanism under test). Bound through an assignment because a parameter named
#: ``strays`` would otherwise read to ruff as a redefinition of the import.
strays = _strays_fixture

#: Prepended to the ``-c`` body of every root here that HOLDS STILL, so a
#: :class:`_Registrar` started before the call can adopt it while the call is in
#: flight (#221 review SITE-4). Without it the two decoding cases below spawned a
#: 60 s sleeper, took ``code=124`` from the raise rather than from any death, and
#: would have stayed GREEN — leaking one sleeper each — with both ``hard_kill``
#: and the ``proc.kill()`` belt deleted. It announces ONE field (these roots have
#: no grandchild); ``os.replace`` makes a half-written line unreadable.
#:
#: ``sys.argv[1]`` is the marker path: with ``-c``, ``argv[0]`` is ``-c``.
_ANNOUNCE_SELF = """\
import os
import sys

_marker = sys.argv[1]
with open(_marker + ".tmp", "w", encoding="utf-8") as _handle:
    _handle.write(f"{os.getpid()}\\n")
os.replace(_marker + ".tmp", _marker)
"""


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[])


def _bound_api_and_harness(
    *,
    tools: list[AgentTool] | None = None,
    project_trusted: bool = True,
) -> tuple[ExtensionAPI, Extension, AgentHarness]:
    """A real ExtensionAPI sharing the harness's runtime (so actions are bound)."""

    rt = _ExtensionRuntime()
    ext = Extension(name="ext5")
    api = ExtensionAPI(ext, rt)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            runtime=rt,
            tools=tools or [],
            project_trusted=project_trusted,
        )
    )
    return api, ext, harness


# === Part (1): runtime introspection / action through a real ExtensionAPI ====


def test_get_commands_returns_registered_commands() -> None:
    api, _ext, _h = _bound_api_and_harness()
    api.register_command("greet", handler=lambda: None, description="say hi")
    cmds = api.get_commands()
    assert any(c.name == "greet" and c.source == "ext5" for c in cmds)
    assert any(c.description == "say hi" for c in cmds)


def test_get_all_tools_returns_harness_tool_set() -> None:
    tool = AgentTool(name="mytool", execute=_noop_execute)
    api, _ext, _h = _bound_api_and_harness(tools=[tool])
    infos = api.get_all_tools()
    names = {t.name for t in infos}
    assert "mytool" in names
    # ToolInfo views, not raw AgentTool instances.
    assert all(hasattr(t, "name") for t in infos)


async def test_exec_runs_command_and_returns_stdout() -> None:
    # exec is the in-process subprocess port (pi ``execCommand``); it works even
    # though the harness binds ``exec`` as a throwing stub (the API falls back).
    api, _ext, _h = _bound_api_and_harness()
    result = await api.exec("echo", ["aelix-exec-ok"])
    assert result.code == 0
    assert "aelix-exec-ok" in result.stdout
    assert result.killed is False


async def test_exec_missing_command_returns_127() -> None:
    api, _ext, _h = _bound_api_and_harness()
    result = await api.exec("this-binary-does-not-exist-aelix", [])
    assert result.code == 127


# === Part (1b): exec is a CONTAINED run (#221) ==============================
#
# The default binding was ``subprocess.run(capture_output=True, timeout=…)``.
# Measured through THIS method on main dff6b62, under a real model: a command
# that outran its timeout had its root killed and nothing else (the sleeper
# survived at ``ppid 1``, still inside aelix's own process group,
# ``survivors=1``), and a command that exited 0 after backgrounding a helper was
# reported as ``code=124 killed=True stdout='done\n'`` a full second after it had
# finished, because ``communicate()`` waits for pipe EOF rather than for the
# root. Both shapes are pinned below against real children.
#
# Every call goes through ``_run_bounded`` — there is no pytest-timeout here and
# no ``timeout-minutes`` on the CI jobs, so a case whose termination depends on a
# bound inside ``run_contained`` would otherwise burn GitHub's 360-minute default
# if that bound were deleted. ``asyncio.run`` inside it gives the coroutine a
# loop of its own on that daemon thread.


def _exec_bounded(
    command: str, args: list[str], *, bound: float, what: str, **kwargs: Any
) -> ExecResult:
    """``api.exec(...)`` on a daemon thread with its own loop, joined for ``bound``."""

    api, _ext, _h = _bound_api_and_harness()
    return _run_bounded(lambda: asyncio.run(api.exec(command, args, **kwargs)), bound, what)


def test_exec_dispatches_to_run_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    # The call the site makes, pinned without spawning anything: argv is
    # ``[command, *args]``, the timeout is SECONDS (the surface takes ms), cwd
    # goes straight through, and env is the parent's environment UPDATED by the
    # caller's — not replaced by it, which is what ``env=`` alone would do.
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def _fake(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, b"out", b"err")

    monkeypatch.setattr(api_mod, "run_contained", _fake)
    monkeypatch.setenv("AELIX_PROBE_INHERITED", "from-the-parent")

    result = _exec_bounded(
        "a-command",
        ["--flag"],
        bound=5.0,
        what="exec dispatch",
        cwd="/some/where",
        env={"AELIX_PROBE_CALLER": "from-the-caller"},
        timeout_ms=1500,
    )

    assert (result.code, result.stdout, result.stderr, result.killed) == (0, "out", "err", False)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["a-command", "--flag"]
    assert set(kwargs) == {"timeout", "cwd", "env", "abort"}
    assert kwargs["timeout"] == 1.5
    assert kwargs["cwd"] == "/some/where"
    assert kwargs["env"]["AELIX_PROBE_INHERITED"] == "from-the-parent"
    assert kwargs["env"] == dict(os.environ) | {"AELIX_PROBE_CALLER": "from-the-caller"}
    # #221 review SITE-1: every call carries a grip, because the interrupt
    # ladder inside ``run_contained`` cannot fire on a ``to_thread`` worker.
    assert isinstance(kwargs["abort"], AbortHandle)


@pytest.mark.parametrize(
    ("timeout_ms", "expected"),
    [(None, None), (0, None), (250, 0.25), (1000, 1.0)],
    ids=["none", "zero", "sub-second", "one-second"],
)
def test_exec_timeout_unit_is_seconds_and_falsy_means_unbounded(
    monkeypatch: pytest.MonkeyPatch, timeout_ms: int | None, expected: float | None
) -> None:
    # ``timeout_ms=0`` is Pi's "no timeout" too, and it must not become
    # ``timeout=0.0`` — which run_contained would treat as a deadline already
    # past and kill the command before it started.
    seen: list[float | None] = []

    def _fake(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(api_mod, "run_contained", _fake)
    _exec_bounded("a-command", [], bound=5.0, what="exec timeout unit", timeout_ms=timeout_ms)
    assert seen == [expected]


def test_exec_timeout_ends_the_pipe_holding_grandchild(tmp_path: Path, strays: list[int]) -> None:
    """The leak this issue exists for, at the documented extension surface.

    On main the timeout killed the ROOT and left the grandchild holding the
    pipes; on Windows CPython's untimed post-kill ``communicate()`` then never
    returned at all, so the ``to_thread`` worker was wedged for as long as that
    grandchild lived. The root announces both pids before it sleeps, so a green
    run here cannot be one that measured a tree that never existed — and the
    registrar adopts them WHILE the call is in flight, because the failure
    ``_run_bounded`` exists to produce is a call that never returns, and
    registering afterwards would leak exactly the tree it just caught (#221
    review T4).
    """

    marker = tmp_path / "pids.txt"
    args = ["-c", ROOT_SOURCE, str(marker), "inherit", str(OUTLIVE), str(OUTLIVE)]
    bound = 1.0 + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 5
    registrar = _registrar(marker, strays)
    started = time.monotonic()

    try:
        result = _exec_bounded(
            sys.executable,
            args,
            bound=bound,
            what="exec timeout with a pipe-holding grandchild",
            timeout_ms=1000,
        )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, grandchild = pids
    assert result.code == 124
    assert result.killed is True
    assert _await_dead(grandchild) != STATE_ALIVE
    assert _await_dead(root_pid) != STATE_ALIVE
    assert elapsed <= bound


def test_exec_with_a_backgrounded_holder_is_the_success_it_was(
    tmp_path: Path, strays: list[int]
) -> None:
    """Bug 2 at this surface: ``code=0 killed=False``, and the tail is kept.

    Measured on main through the real ``exec`` under a real model: the same
    shape came back ``code=124 killed=True stdout='done\\n'`` after the whole
    timeout. The holder is still ALIVE afterwards — ``kill_on_close=False`` and
    a releasing ``close()`` are what preserve a helper the command deliberately
    backgrounded (the ``strays`` fixture reaps it).
    """

    marker = tmp_path / "pids.txt"
    leaving = tmp_path / "leaving.flag"
    args = ["-c", ROOT_WITH_A_LATE_TAIL, str(marker), str(leaving)]
    ceiling = 0.5 + EXIT_DRAIN_SECONDS + 2.0
    registrar = _registrar(marker, strays)
    started = time.monotonic()

    try:
        result = _exec_bounded(
            sys.executable, args, bound=ceiling + 5, what="exec with a backgrounded holder"
        )
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    _root_pid, holder = pids
    assert result.code == 0
    assert result.killed is False
    assert result.stdout == "done\nlate\n"
    assert elapsed <= ceiling
    assert probe_state(holder) == STATE_ALIVE


def test_exec_replaces_undecodable_bytes_on_the_success_path() -> None:
    # ``text=True`` was the LOCALE codec with STRICT errors, so one non-decodable
    # byte raised UnicodeDecodeError out of exec — measurably reachable. utf-8
    # with replacement converges on Pi (``data.toString()``).
    result = _exec_bounded(
        sys.executable,
        ["-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\n')"],
        bound=30.0,
        what="undecodable bytes on the success path",
    )
    assert result.code == 0
    assert result.stdout == "ok �\n"


def test_exec_replaces_undecodable_bytes_on_the_timeout_path(
    tmp_path: Path, strays: list[int]
) -> None:
    # main's TIMEOUT path was the odd one out: it attached raw bytes and decoded
    # them with ``.decode()`` (strict utf-8), so the same byte raised there too —
    # from inside the except branch, where nothing was left to catch it.
    #
    # The root ANNOUNCES itself and the case asserts it DIED (#221 review
    # SITE-4). ``code=124`` comes from the raise, not from a death: traced with
    # both ``hard_kill`` and the ``proc.kill()`` belt removed, the reap's own
    # ``TimeoutExpired`` is suppressed and the raise still happens, so this case
    # stayed green — while leaking one 60 s sleeper per run.
    marker = tmp_path / "pid.txt"
    body = (
        _ANNOUNCE_SELF + "import time\n"
        "sys.stdout.buffer.write(b'\\xff')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    registrar = _registrar(marker, strays, fields=1)

    try:
        result = _exec_bounded(
            sys.executable,
            ["-c", body, str(marker), MARK],
            bound=0.5 + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 5,
            what="undecodable bytes on the timeout path",
            timeout_ms=500,
        )
    finally:
        pids = registrar.settle()

    assert pids is not None, "the root never announced itself — the case measured nothing"
    (root_pid,) = pids
    assert result.code == 124
    assert result.killed is True
    assert result.stdout == "\N{REPLACEMENT CHARACTER}"
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, f"root {root_pid} still {root_state} after {DEADLINE}s"


def test_exec_translates_crlf_on_both_paths(tmp_path: Path, strays: list[int]) -> None:
    # ``text=True`` was also io.TextIOWrapper(newline=None), so the success path
    # already normalised line endings and the timeout path did not. Both do now
    # (#221 review POSIX-5/DOC-15) — the divergence from Pi's raw \r\n is
    # declared and deliberate.
    body = "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\rc\\n')"
    success = _exec_bounded(
        sys.executable, ["-c", body], bound=30.0, what="crlf on the success path"
    )
    assert success.stdout == "a\nb\nc\n"

    # The timeout arm announces its root and asserts it died, for the same reason
    # as the case above (#221 review SITE-4): 124 is the raise, not the death.
    marker = tmp_path / "pid.txt"
    outliving = (
        _ANNOUNCE_SELF + "import time\n"
        "sys.stdout.buffer.write(b'a\\r\\nb\\rc\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    registrar = _registrar(marker, strays, fields=1)

    try:
        timed_out = _exec_bounded(
            sys.executable,
            ["-c", outliving, str(marker), MARK],
            bound=0.5 + REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS + 5,
            what="crlf on the timeout path",
            timeout_ms=500,
        )
    finally:
        pids = registrar.settle()

    assert pids is not None, "the root never announced itself — the case measured nothing"
    (root_pid,) = pids
    assert timed_out.code == 124
    assert timed_out.stdout == "a\nb\nc\n"
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, f"root {root_pid} still {root_state} after {DEADLINE}s"


def test_exec_gives_the_command_no_stdin() -> None:
    """Pi's contract (``stdio: ["ignore", "pipe", "pipe"]``), pinned observably.

    Until #221 this INHERITED the TUI's stdin, so an extension command that read
    stdin competed with the editor for the user's keystrokes.

    THE ASSERTION IS ONLY WORTH ANYTHING INSIDE :func:`_with_planted_stdin`
    (#221 review SITE-2). Under pytest's default ``--capture=fd`` this process's
    fd 0 already IS ``/dev/null`` (``_pytest/capture.py``, ``FDCaptureBase``:
    ``if targetfd == 0``), so a child that merely INHERITED stdin also reads
    ``''`` — the naive form passed unchanged against ``main``'s
    ``subprocess.run``-without-``stdin=`` shape (mutated here: 1 passed in
    0.05 s). With readable bytes planted on fd 0 the mutant reads
    ``'SHOULD NOT BE READ\\n'`` and this case is red. The plant does not
    discriminate on win32 — CPython's ``Popen._get_handles`` takes an inherited
    stdin from ``GetStdHandle(STD_INPUT_HANDLE)``, not from fd 0 — and that
    arm's guard is the kwarg case in ``tests/process_tree/test_run_contained.py``.
    """

    result = _with_planted_stdin(
        lambda: _exec_bounded(
            sys.executable,
            ["-c", "import sys; sys.stdout.write(repr(sys.stdin.read()))"],
            bound=30.0,
            what="stdin is devnull",
        )
    )
    assert result.code == 0
    assert result.stdout == "''"


def test_a_cancelled_exec_ends_its_command(tmp_path: Path, strays: list[int]) -> None:
    """Esc, or a ^C in ``aelix -p``: the turn is cancelled and the command dies.

    THE REGRESSION THIS CLOSES IS ONE #221 ITSELF INTRODUCED (review SITE-1).
    This method awaits ``run_contained`` on an ``asyncio.to_thread`` worker and
    CPython delivers a POSIX signal to the MAIN thread only, so the helper's own
    interrupt ladder can never fire here; and the contained spawn's session took
    the child out of the terminal's foreground group, so the tty's ^C stopped
    reaching it either way. Measured: ``0.02 s`` to end the command on ``main``
    dff6b62 against the command's whole remaining life — ``28.58 s`` of a 30 s
    child — contained and without the :class:`AbortHandle`, with
    ``Runner.close``'s 300 s executor join and
    ``concurrent.futures.thread._python_exit``'s unbounded join as the ceiling.

    ``timeout_ms`` is left unset — ``exec``'s default, and what makes the claim
    sharp: nothing else bounds this call at all. THE MUTANT IT KILLS is deleting
    ``abort.abort()`` from ``exec``'s ``except asyncio.CancelledError``; the
    sleeper then outlives ``_await_dead`` and ``asyncio.run``'s executor join
    holds the daemon thread past ``bound``.

    The cancel is fired 0.5 s after the root ANNOUNCED, not 0.5 s after the
    call: aimed by the clock alone it can land before the fork, and the case
    would then measure a kill with nothing under it.
    """

    marker = tmp_path / "pids.txt"
    announced = threading.Event()
    registrar = _registrar(marker, strays, on_announce=lambda _pids: announced.set())
    api, _ext, _harness = _bound_api_and_harness()
    args = ["-c", ROOT_SOURCE, str(marker), "inherit", str(OUTLIVE), str(OUTLIVE)]

    async def _drive() -> None:
        task = asyncio.create_task(api.exec(sys.executable, args))
        # ``announced`` is a threading.Event because the registrar sets it from
        # its own thread; waiting on it off the loop keeps the loop free to run
        # the task that is doing the spawning.
        assert await asyncio.to_thread(announced.wait, 5.0), (
            "the root never announced its tree — the case measured nothing"
        )
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    try:
        _run_bounded(lambda: asyncio.run(_drive()), 20.0, "a cancelled exec")
    finally:
        elapsed = time.monotonic() - started
        pids = registrar.settle()

    assert pids is not None, "the root never announced its tree — the case measured nothing"
    root_pid, grandchild = pids
    root_state = _await_dead(root_pid)
    assert root_state != STATE_ALIVE, (
        f"root {root_pid} still {root_state} after {DEADLINE}s; the cancelled turn "
        f"came back at {elapsed:.3f}s"
    )
    grandchild_state = _await_dead(grandchild)
    assert grandchild_state != STATE_ALIVE, (
        f"grandchild {grandchild} still {grandchild_state} after {DEADLINE}s; the "
        f"cancelled turn came back at {elapsed:.3f}s"
    )


# === Part (2a): ctx.is_project_trusted() reflects harness trust state ========


def test_ctx_is_project_trusted_default_true() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=True)
    ctx = h._make_context()
    assert ctx.is_project_trusted() is True


def test_ctx_is_project_trusted_false_when_untrusted() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=False)
    ctx = h._make_context()
    assert ctx.is_project_trusted() is False


def test_set_project_trusted_is_reflected_live() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=True)
    assert h._make_context().is_project_trusted() is True
    h.set_project_trusted(False)
    # A fresh context built after the flip sees the new state.
    assert h._make_context().is_project_trusted() is False
    assert h.project_trusted is False


def test_command_context_also_reports_trust() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=False)
    cmd_ctx = h.make_command_context()
    assert cmd_ctx.is_project_trusted() is False


# === Part (2b): emit_project_trust_event walk semantics =====================


def _trust_ctx() -> ProjectTrustContext:
    return ProjectTrustContext(cwd="/proj", mode="interactive", has_ui=True)


async def test_emit_first_yes_no_decision_wins() -> None:
    ext = Extension(name="voter")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    def decide(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="no", remember=True)

    api.on("project_trust", decide)
    result, errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert errors == []
    assert result is not None
    assert result.trusted == "no"
    assert result.remember is True


async def test_emit_undecided_falls_through() -> None:
    ext = Extension(name="abstain")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    def abstain(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="undecided")

    api.on("project_trust", abstain)
    result, errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is None
    assert errors == []


async def test_emit_async_handler_supported() -> None:
    ext = Extension(name="async-voter")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    async def decide(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="yes")

    api.on("project_trust", decide)
    result, _errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is not None and result.trusted == "yes"


async def test_emit_handler_error_collected_not_raised() -> None:
    bad = Extension(name="boom")
    bad_api = ExtensionAPI(bad, _ExtensionRuntime())

    def explode(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        raise RuntimeError("kaboom")

    bad_api.on("project_trust", explode)

    good = Extension(name="good")
    good_api = ExtensionAPI(good, _ExtensionRuntime())
    good_api.on(
        "project_trust",
        lambda e, c: ProjectTrustEventResult(trusted="yes"),
    )

    # The bad extension's error is collected; the next extension still decides.
    result, errors = await emit_project_trust_event(
        [bad, good], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is not None and result.trusted == "yes"
    assert len(errors) == 1
    assert "boom" in errors[0] and "kaboom" in errors[0]


# === Part (2c): resolve_project_trusted honours the event + default =========


def _resources_dir(tmp_path: Path) -> Path:
    aelix = tmp_path / ".aelix"
    aelix.mkdir(parents=True)
    (aelix / "mcp.json").write_text('{"mcpServers": {}}')
    return tmp_path


def _voting_extension(
    decision: ProjectTrustEventDecision, *, remember: bool | None = None
) -> Extension:
    ext = Extension(name=f"vote-{decision}")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on(
        "project_trust",
        lambda e, c: ProjectTrustEventResult(trusted=decision, remember=remember),
    )
    return ext


async def test_resolve_event_yes_trusts_before_store(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(cwd, False)  # store says NO, but the event decides first.
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=store,
        extensions=[_voting_extension("yes")],
    )
    assert out is True


async def test_resolve_event_remember_persists(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=store,
        extensions=[_voting_extension("no", remember=True)],
    )
    assert out is False
    # remember=True wrote the decision to disk.
    assert store.get(cwd) is False


async def test_resolve_event_undecided_falls_through_to_deny(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,  # headless → deny-by-default after the event abstains
        store=ProjectTrustStore(tmp_path / "agent"),
        extensions=[_voting_extension("undecided")],
    )
    assert out is False


async def test_resolve_event_errors_reported(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    bad = Extension(name="boom")
    ExtensionAPI(bad, _ExtensionRuntime()).on(
        "project_trust",
        lambda e, c: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    seen: list[str] = []
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=ProjectTrustStore(tmp_path / "agent"),
        extensions=[bad],
        on_extension_error=seen.append,
    )
    assert out is False  # handler raised → no decision → headless deny
    assert len(seen) == 1 and "nope" in seen[0]


async def test_resolve_default_always_trusts_without_prompt(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="always",
    )
    assert out is True


async def test_resolve_default_never_denies(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)

    async def _prompt(_c: Path) -> Any:
        raise AssertionError("must not prompt when default is 'never'")

    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="never",
    )
    assert out is False


async def test_resolve_default_ask_falls_through_to_prompt(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    from aelix_coding_agent.cli.project_trust import ProjectTrustPromptResult

    async def _prompt(_c: Path) -> ProjectTrustPromptResult:
        return ProjectTrustPromptResult(trusted=True, remember=False)

    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="ask",
    )
    assert out is True
