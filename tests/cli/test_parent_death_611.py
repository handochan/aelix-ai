"""#777 (#611 S3c): what a killed aelix leaves of its background jobs, read by pid and start time.

PRs c1 and c2 of the issue land as one PR with two commits (리드 판단, the lead's
call), so this file holds both.

A real CLI, started through the gate-on launcher (``tests/jobs/gated_cli.py``)
against a local chat-completions SSE server, runs ``-p`` with two jobs: a
background delegation (``agent(action="spawn")``) whose child runs a foreground
``bash`` that blocks on a sleeper, and a background ``bash`` that runs a
sleeper. Every process a case watches announces its pid and its start time
(:func:`~aelix_ai.utils._process_identity.process_started`) in a marker file:

* ``parent`` and ``child``, the two aelix interpreters themselves — the
  launcher and the gated child's ``sitecustomize`` write them
  (``AELIX_TEST_ANNOUNCE``). On the windows runner ``sys.executable`` is the venv
  launcher, so neither is a ``Popen``'s pid;
* ``bg-sleeper`` and ``child-sleeper``, and the shell each runs under
  (``bg-shell``, ``child-shell``), which the sleeper names from ``$$`` or
  ``$PID`` — the shell's own pid, not its parent's.

The positive control is that every one of them is alive, with the announced
start time, before the kill; an unknown start time fails it. Then the parent is
killed, and a process counts as ended only when it is first read ended within
the bound, counted from the kill:

* **L1, Linux.** SIGKILL. The delegated child and the background shell are gone
  within 5 s: ``pdeathsig`` on each (``print_channel.py``, and
  ``bash_background.py`` on the shell only). What the two shells run is
  recorded with ``warnings.warn``, not asserted: the owner's OQ-C/D answer
  (2026-11-10, "둘 다 권고대로 (권장)") puts background bash's ``pdeathsig`` on
  the shell only, so its command's children may outlive a SIGKILLed aelix
  (ADR-0297), and the child's own commands end only through the child's own
  cleanup (ADR-0291 limit 3).
* **L2, Windows.** Each announced process is opened before the kill with
  ``SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION`` (and ``PROCESS_TERMINATE``,
  so the case can end it): no Job handle is held — the Jobs have no name and
  aelix holds their last handle (``_process_tree.py``). Every handle must be
  unsignalled right before ``TerminateProcess`` of the parent, which must
  succeed, and signalled within 10 s after it (ADR-0238's kill-on-close). This
  proves the ANNOUNCED processes only, not that a Job is empty; and the
  delegated child also ends when the venv launcher's own job kills the
  interpreter it started, so its row does not tell which job did. A failure
  says, per process, its image and parent pid, whether it was in any job
  (``IsProcessInJob(h, NULL)``), its creation time, when it ended and its exit
  code, and what ``TerminateProcess`` returned.
* **L3, macOS.** SIGKILL, then the survivors are recorded and killed. Nothing
  is asserted about the children: macOS has no ``pdeathsig`` (ADR-0291 limit 3).

**PR c2, L1a–L3a: a job Ctrl+B moved.** The same, with the TUI instead of
``-p``: ``tests/jobs/gated_tui_child.py`` runs the launcher's gated CLI on its
TUI path, its keys read from stdin. The parent runs one agent in the
foreground; once the child's sleeper announced itself, the case writes Ctrl+B
(``\x02``), and once the parent's next request carries the moved call's answer
it kills that aelix, and reads the moved job's processes as L1–L3 do: on Linux
the delegated child is gone within 5 s, on Windows every announced handle is
signalled within 10 s (a moved job's evidence is its root only, ADR-0298; this
reads the processes themselves), and macOS is recorded. **L1b–L3b** are the
same TUI delegation left in the turn, not moved: the spawn every TUI delegation
takes. On windows-latest at ``b69890da`` L2a failed on all four legs, the
child's shell and sleeper alive 10 s after the kill (run 43998348364), and #777's
fix round 1 spawns the print channel's child suspended, so it runs only once it
is in its Job.

The child runs ``bash`` only because the parent runs ``--permission-mode yolo``: a
default parent clamps a delegated child to ``plan`` (``aelix_agents/consent.py``),
whose ``bash`` is refused. The model is scripted, in a scratch HOME and cwd.

Every case kills, at its end, every announced process still alive with its
announced start time, and nothing else, the parent first; it rescans the
markers until none has appeared for a second and waits for what it killed (an
announcement made after it returns is past any rescan);
during the case a pid is read by identity (``tests/rpc/_identity.py``), so a
reused number never reads as alive. ``tests/cli/test_parent_death_checks_777.py``
pins these checks against fakes.

On POSIX these are guards of ``b6100968``'s behaviour, and what a case needs
that base lacks is test-only (the launchers' announcement). On Windows L2a and
L2b need #777's suspended print-channel spawn.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_identity import process_started

from tests.env_sandbox import child_env
from tests.jobs.gated_cli import ANNOUNCE
from tests.jobs.test_gated_cli_611 import (
    LAUNCHER,
    PROFILE,
    ModelServer,
    Scratch,
    Turn,
    call,
    first_user,
    say,
    text_of,
)
from tests.process_probe import STATE_ALIVE, probe_state
from tests.rpc._identity import ENDED, Identities, same_or_reused

LINUX = pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux (L1)")
WIN32 = pytest.mark.skipif(sys.platform != "win32", reason="a real Windows Job (L2)")
DARWIN = pytest.mark.skipif(sys.platform != "darwin", reason="macOS, recorded only (L3)")

SLEEP_SECONDS = 120
"""How long a sleeper sleeps: past every case's end, and an end to what a failed cleanup leaves."""

ANNOUNCE_BOUND = 90.0
"""How long a case waits for every process to announce itself (the windows runner is slow to start one)."""

CLEANUP_QUIET = 1.0
"""How long the cleanup's rescan must find no new announcement before it stops."""

CLEANUP_BOUND = 20.0
"""How long the cleanup waits for what it killed to end."""

#: A sleeper that announces the shell that runs it (its pid is an argument:
#: ``$$`` or ``$PID``) and then itself, each file whole at its ``os.replace``.
_SLEEPER = """\
import json, os, sys, time
from aelix_ai.utils._process_identity import process_started

job, where, seconds, shell = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])


def announce(name, pid):
    path = os.path.join(where, "%s-%d.json" % (name, pid))
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"role": name, "pid": pid, "started": process_started(pid)}, handle)
    os.replace(path + ".tmp", path)


announce(job + "-shell", shell)
announce(job + "-sleeper", os.getpid())
time.sleep(seconds)
"""

#: What a -p run watches: the two aelix interpreters, both shells and both sleepers.
PRINT_ROLES = ("parent", "child", "child-shell", "child-sleeper", "bg-shell", "bg-sleeper")

#: What a TUI run watches once Ctrl+B moved its foreground delegation (#777 PR c2).
TUI_ROLES = ("parent", "child", "child-shell", "child-sleeper")

TUI_CHILD = Path(__file__).resolve().parents[1] / "jobs" / "gated_tui_child.py"

MOVED = "Moved to the background at the user's request"
"""The moved call's answer (``aelix_agents/tool.py``): the parent's next request carries it."""


@dataclass(frozen=True)
class Announced:
    role: str
    pid: int
    started: float | None


class Server(ModelServer):
    """:class:`~tests.jobs.test_gated_cli_611.ModelServer` whose delegated child is scripted too."""

    def __init__(self, parent_turn: Turn, child_turn: Turn) -> None:
        super().__init__(parent_turn)
        self._child_turn = child_turn

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if text_of(first_user(body)).startswith("Task: "):
                self.child.append(body)
                return self._child_turn(body, len(self.child) - 1)
            self.parent.append(body)
            return self._parent_turn(body, len(self.parent) - 1)


@dataclass
class Run:
    """One gated aelix in a scratch, the processes it announced, and what the case read of them."""

    tmp_path: Path
    scratch: Scratch = field(init=False)
    marks: Path = field(init=False)
    env: dict[str, str] = field(init=False)
    proc: subprocess.Popen[bytes] | None = None
    ids: Identities = field(default_factory=Identities)
    seen: dict[str, Announced] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.scratch = Scratch(self.tmp_path)
        self.marks = self.tmp_path / "marks"
        self.marks.mkdir()
        self.sleeper = self.tmp_path / "announce_sleeper.py"
        self.sleeper.write_text(_SLEEPER, encoding="utf-8")
        self.env = child_env(
            self.scratch.home,
            XDG_CONFIG_HOME=str(self.scratch.home / ".config"),
            AELIX_CODING_AGENT_DIR=str(self.scratch.agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
            AELIX_MAX_RETRIES="0",
            **{ANNOUNCE: str(self.marks)},
        )

    def command(self, job: str) -> str:
        """A ``bash`` command line that runs the sleeper as ``<job>-sleeper`` under the shell aelix
        resolves here; it names that shell ``<job>-shell``."""

        from aelix_ai.utils._shell import POWERSHELL_NAMES, shell_basename
        from aelix_coding_agent.tools.bash import _resolve_shell

        name = shell_basename(_resolve_shell(dict(self.env)).path)
        args = (sys.executable, str(self.sleeper), job, str(self.marks), str(SLEEP_SECONDS))
        if name in POWERSHELL_NAMES:
            return "& " + " ".join(f"'{a}'" for a in args) + " $PID"
        assert sys.platform != "win32", f"no sleeper command line for the shell {name!r} on win32"
        # ``; true`` keeps the shell the sleeper's parent: a lone command may be exec'd.
        return " ".join(shlex.quote(a) for a in args) + " $$; true"

    def start(
        self, entry: Path, *args: str, stdin: int = subprocess.DEVNULL
    ) -> subprocess.Popen[bytes]:
        argv = [sys.executable, str(entry), "--agents", "--provider", "mockp", "--model", "mockm"]
        argv += ["--permission-mode", "yolo", *args]
        self.stdout = (self.tmp_path / "stdout.txt").open("wb")
        self.stderr = (self.tmp_path / "stderr.txt").open("wb")
        self.proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=self.scratch.cwd,
            env=self.env,
            stdin=stdin,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        return self.proc

    def announced(self) -> dict[str, Announced]:
        found: dict[str, Announced] = {}
        for path in self.marks.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # mid-replace on win32: not yet
                continue
            found[data["role"]] = Announced(data["role"], int(data["pid"]), data["started"])
        return found

    def stderr_tail(self) -> str:
        with contextlib.suppress(OSError):
            return (self.tmp_path / "stderr.txt").read_text(encoding="utf-8", errors="replace")[
                -3000:
            ]
        return ""

    def wait_for(
        self, roles: tuple[str, ...], bound: float = ANNOUNCE_BOUND
    ) -> dict[str, Announced]:
        """Every role in ``roles`` announced, or the case fails saying which were and what aelix said."""

        deadline = time.monotonic() + bound
        while True:
            self.seen.update(self.announced())
            if set(roles) <= set(self.seen):
                return {role: self.seen[role] for role in roles}
            exited = self.proc is not None and self.proc.poll() is not None
            if exited or time.monotonic() > deadline:
                pytest.fail(
                    f"announced {sorted(self.seen)} of {list(roles)} "
                    f"({'aelix exited ' + str(self.proc.returncode) if exited and self.proc else 'timed out'}); "
                    f"stderr: {self.stderr_tail()}"
                )
            time.sleep(0.05)

    def positive_control(self, procs: dict[str, Announced]) -> None:
        """Each announced process is alive and is the one that announced: its start time, read now
        and announced, is known and the same. An unknown one fails (Codex round 1, L4)."""

        self.ids.record([p.pid for p in procs.values()])
        wrong = {
            role: (p.pid, self.ids.at[p.pid], p.started)
            for role, p in procs.items()
            if p.started is None or self.ids.at[p.pid] != (STATE_ALIVE, p.started)
        }
        assert not wrong, (
            f"before the kill, (pid, (state, start time now), start time announced): {wrong}"
        )

    def state(self, pid: int) -> str:
        """``alive``, or one of :data:`~tests.rpc._identity.ENDED` — by identity, never by number alone."""

        state = probe_state(pid)
        if state == STATE_ALIVE and self.ids.reused(pid):
            return "reused"
        return state

    def watch(
        self, procs: dict[str, Announced], seconds: float, *, since: float
    ) -> dict[str, tuple[str, float | None]]:
        """Each process's state ``seconds`` after ``since`` (``time.monotonic()``), and how long
        after ``since`` it was first read ended."""

        ended: dict[str, float] = {}
        while True:
            for role, p in procs.items():
                if role not in ended and self.state(p.pid) in ENDED:
                    ended[role] = round(time.monotonic() - since, 3)
            if len(ended) == len(procs) or time.monotonic() - since >= seconds:
                break
            time.sleep(0.05)
        return {role: (self.state(p.pid), ended.get(role)) for role, p in procs.items()}

    def clean_up(self, *, quiet: float = CLEANUP_QUIET, bound: float = CLEANUP_BOUND) -> list[str]:
        """Kill every announced process alive with its announced start time, the parent first; wait
        for each to end; rescan the markers until none has appeared for ``quiet`` s; reap the
        launcher; close its pipes. What is still alive after ``bound`` is returned, never killed
        blind: a number another process holds now is never signalled (Codex round 1, M2)."""

        if self.proc is not None and self.proc.stdin is not None:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()  # the pipe a case writes keys to (verify F6)
        owned: dict[int, Announced] = {}
        deadline = time.monotonic() + bound
        last_new = time.monotonic()
        while True:
            fresh = [p for p in self.announced().values() if p.pid not in owned]
            for p in sorted(
                fresh, key=lambda a: a.role != "parent"
            ):  # no new spawn: the parent first
                owned[p.pid] = p
                if _is_itself(p):
                    _kill(p.pid)
            if fresh:
                last_new = time.monotonic()
            alive = [p for p in owned.values() if _is_itself(p)]
            now = time.monotonic()
            if (not alive and now - last_new >= quiet) or now >= deadline:
                break
            time.sleep(0.05)
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=20)
        for handle in (getattr(self, "stdout", None), getattr(self, "stderr", None)):
            if handle is not None:
                handle.close()
        return [f"{p.role} pid {p.pid}" for p in owned.values() if _is_itself(p)]


def _is_itself(p: Announced) -> bool:
    """Whether ``p.pid`` is alive and still the process that announced (known, equal start time)."""

    if p.started is None or probe_state(p.pid) != STATE_ALIVE:
        return False
    return process_started(p.pid) == p.started


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)  # TerminateProcess on win32
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


@contextlib.contextmanager
def running(
    tmp_path: Path, turns: Callable[[Run], tuple[Turn, Turn]]
) -> Iterator[tuple[Run, Server]]:
    """A :class:`Run` and its server, whose turns ``turns(run)`` scripts; the run's processes are
    cleaned up whatever the case did."""

    run = Run(tmp_path)
    server = Server(*turns(run))
    with server:
        run.scratch.point_at(server)
        try:
            yield run, server
        finally:
            left = run.clean_up()
            if left:  # never raised from here: it would hide the case's own failure
                warnings.warn(f"the cleanup could not end {left}", stacklevel=3)


def print_turns(run: Run) -> tuple[Turn, Turn]:
    """The parent spawns an agent, then starts a background sleeper, then waits; the child runs its sleeper."""

    def parent(_body: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 0:
            return call("agent", {"action": "spawn", "profile": PROFILE, "task": "run the sleeper"})
        if index == 1:
            return call(
                "bash", {"command": run.command("bg"), "run_in_background": True}, "call_bg"
            )
        return say(f"parent turn {index}")

    return parent, child_turn(run)


def child_turn(run: Run) -> Turn:
    def child(_body: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 0:
            return call("bash", {"command": run.command("child")}, "call_child")
        return say("the sleeper ended")

    return child


def started_print_run(run: Run) -> dict[str, Announced]:
    """``-p`` through the launcher; every :data:`PRINT_ROLES` process announced and alive."""

    run.start(LAUNCHER, "-p", "go")
    procs = run.wait_for(PRINT_ROLES)
    run.positive_control(procs)
    return procs


def describe(
    procs: dict[str, Announced], states: dict[str, tuple[str, float | None]], run: Run
) -> str:
    rows = []
    for role, p in procs.items():
        state, at = states[role]
        when = f"ended {at} s after the kill" if at is not None else "never read ended"
        rows.append(f"{role} pid {p.pid}: {state}, {when}; {same_or_reused(p.pid, run.ids)}")
    return "; ".join(rows)


def sigkill(run: Run, parent: Announced) -> float:
    """SIGKILL the parent and reap it; when it was sent (``time.monotonic()``)."""

    if sys.platform == "win32":  # pragma: no cover - the POSIX legs only
        raise AssertionError("SIGKILL is POSIX")
    killed = time.monotonic()
    os.kill(parent.pid, signal.SIGKILL)
    assert run.proc is not None
    run.proc.wait(timeout=20)
    return killed


def late_or_alive(
    states: dict[str, tuple[str, float | None]], roles: tuple[str, ...], bound: float
) -> list[str]:
    """``roles`` not read ended within ``bound`` s of the kill (Codex round 1, M1).

    A first read of "ended" after the bound fails too: it cannot tell whether the
    process ended inside the bound, and the time spent reaping the parent counts,
    since the clock starts at the kill.
    """

    late = [role for role in roles if (at := states[role][1]) is None or at > bound]
    return late


def sigkill_on_linux(
    run: Run,
    procs: dict[str, Announced],
    *,
    label: str,
    asserted: tuple[str, ...],
    bound: float = 5.0,
) -> None:
    """SIGKILL the parent: ``asserted`` are read gone within ``bound`` s (``pdeathsig``); the others are recorded."""

    killed = sigkill(run, procs["parent"])
    children = {role: p for role, p in procs.items() if role != "parent"}
    states = run.watch(children, bound, since=killed)
    warnings.warn(
        f"{label}, {bound} s after the SIGKILL; {', '.join(asserted)} asserted, the rest recorded only "
        f"(OQ-C, ADR-0297, ADR-0291 limit 3): " + describe(children, states, run),
        stacklevel=2,
    )
    left = late_or_alive(states, asserted, bound)
    assert not left, f"{left} not read gone within {bound} s of the SIGKILL: " + describe(
        children, states, run
    )


def sigkill_on_macos(run: Run, procs: dict[str, Announced], *, label: str) -> None:
    """SIGKILL the parent and record what is left after 5 s: no ``pdeathsig`` on macOS."""

    killed = sigkill(run, procs["parent"])
    children = {role: p for role, p in procs.items() if role != "parent"}
    states = run.watch(children, 5.0, since=killed)
    warnings.warn(
        f"{label}, 5 s after the SIGKILL, recorded only (ADR-0291 limit 3): "
        + describe(children, states, run),
        stacklevel=2,
    )
    assert run.state(procs["parent"].pid) in ENDED, "the parent itself was killed"


def terminate_on_windows(
    procs: dict[str, Announced], *, bound: float = 10.0, kernel: Any = None
) -> dict[str, Any]:
    """Handles on every announced process, then ``TerminateProcess`` the parent: each signalled within ``bound`` s.

    Before the kill every handle must be unsignalled, and ``TerminateProcess``
    must return success (Codex round 1, M3); a handle read signalled only after
    the bound fails (M1). It proves the announced processes only, not that a Job
    is empty. The report says, per process, its image and parent pid, whether
    it was in any job (``IsProcessInJob(h, NULL)``), its creation time, when it
    ended and its exit code (verify F3).
    """

    with WinHandles(procs, kernel=kernel) as handles:
        report = handles.terminate_and_watch("parent", bound)
    shown = json.dumps(report, indent=1)
    assert not report["ended_before_the_kill"], (
        "ended before the kill, so the kill proves nothing: " + shown
    )
    assert report["terminate"]["returned"], "TerminateProcess failed: " + shown
    rows = report["processes"]
    left = [
        role
        for role, row in rows.items()
        if row["ended_after"] is None or row["ended_after"] > bound
    ]
    assert not left, f"{left} not read ended within {bound} s of TerminateProcess: " + shown
    return report


@LINUX
def test_l1_a_sigkilled_parents_delegated_child_and_background_shell_are_gone_within_5_s(
    tmp_path: Path,
) -> None:
    with running(tmp_path, print_turns) as (run, _server):
        procs = started_print_run(run)
        sigkill_on_linux(run, procs, label="L1 (Linux)", asserted=("child", "bg-shell"))


@WIN32
def test_l2_a_terminated_parents_announced_processes_all_end_within_10_s(tmp_path: Path) -> None:
    """Both Jobs' kill-on-close, or for the child also the venv launcher's own job: either ends it.

    The assertion covers the announced processes only, not that a Job is empty.
    """

    with running(tmp_path, print_turns) as (run, _server):
        terminate_on_windows(started_print_run(run))


@DARWIN
def test_l3_a_sigkilled_parents_jobs_on_macos_are_recorded_and_cleaned_up(tmp_path: Path) -> None:
    """macOS has no ``pdeathsig``: the survivors are the documented limit, recorded and then killed."""

    with running(tmp_path, print_turns) as (run, _server):
        sigkill_on_macos(run, started_print_run(run), label="L3 (macOS)")


# ── PR c2: a TUI delegation, moved by Ctrl+B or not, then the parent's death ──


def tui_turns(run: Run) -> tuple[Turn, Turn]:
    """The parent runs one agent in the foreground, then answers; the child runs its sleeper."""

    def parent(_body: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 0:
            return call("agent", {"profile": PROFILE, "task": "run the sleeper"})
        return say(f"parent turn {index}")

    return parent, child_turn(run)


def tui_run(run: Run, server: Server, *, move: bool) -> dict[str, Announced]:
    """The TUI child: ``go`` submitted, and its delegated child's sleeper announced. With ``move``,
    Ctrl+B then, the move confirmed by the parent's next request, which carries the moved call's
    answer; without it, the call stays in the turn (L1b–L3b)."""

    proc = run.start(TUI_CHILD, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    run.wait_for(("parent",))
    proc.stdin.write(b"go\r")
    proc.stdin.flush()
    procs = run.wait_for(TUI_ROLES)
    run.positive_control(procs)
    if move:
        proc.stdin.write(b"\x02")
        proc.stdin.flush()
        deadline = time.monotonic() + ANNOUNCE_BOUND
        while not any(
            MOVED in text_of(m) for b in server.parent[1:] for m in b.get("messages", [])
        ):
            if proc.poll() is not None or time.monotonic() > deadline:
                pytest.fail(
                    f"no move: {len(server.parent)} parent requests, aelix "
                    f"{'exited ' + str(proc.returncode) if proc.poll() is not None else 'running'}; "
                    f"stderr: {run.stderr_tail()}"
                )
            time.sleep(0.05)
    else:
        assert len(server.parent) == 1, "the foreground call has not returned: it is not moved"
    assert run.state(procs["child"].pid) == STATE_ALIVE, "the delegated child runs at the kill"
    return procs


@LINUX
def test_l1a_a_sigkilled_tui_parents_moved_delegated_child_is_gone_within_5_s(
    tmp_path: Path,
) -> None:
    with running(tmp_path, tui_turns) as (run, server):
        procs = tui_run(run, server, move=True)
        sigkill_on_linux(run, procs, label="L1a (Linux, moved)", asserted=("child",))


@WIN32
def test_l2a_a_terminated_tui_parents_moved_job_processes_all_end_within_10_s(
    tmp_path: Path,
) -> None:
    """A moved run keeps the print channel's row and its Job, whose kill-on-close ends it (ADR-0238).

    Red on windows-latest at ``b69890da`` (run 43998348364, all four legs): the
    child's shell and sleeper outlived the kill. The print channel's child now
    runs only once it is in its Job (#777 fix round 1).
    """

    with running(tmp_path, tui_turns) as (run, server):
        terminate_on_windows(tui_run(run, server, move=True))


@DARWIN
def test_l3a_a_sigkilled_tui_parents_moved_job_on_macos_is_recorded_and_cleaned_up(
    tmp_path: Path,
) -> None:
    with running(tmp_path, tui_turns) as (run, server):
        sigkill_on_macos(run, tui_run(run, server, move=True), label="L3a (macOS, moved)")


@LINUX
def test_l1b_a_sigkilled_tui_parents_foreground_delegated_child_is_gone_within_5_s(
    tmp_path: Path,
) -> None:
    """The TUI path with no move: the same spawn, still in the turn (verify round 1's entry points)."""

    with running(tmp_path, tui_turns) as (run, server):
        procs = tui_run(run, server, move=False)
        sigkill_on_linux(run, procs, label="L1b (Linux, foreground)", asserted=("child",))


@WIN32
def test_l2b_a_terminated_tui_parents_foreground_delegation_processes_all_end_within_10_s(
    tmp_path: Path,
) -> None:
    """The unmoved TUI delegation: the same spawn L2a found escaping its Job, measured without Ctrl+B."""

    with running(tmp_path, tui_turns) as (run, server):
        terminate_on_windows(tui_run(run, server, move=False))


@DARWIN
def test_l3b_a_sigkilled_tui_parents_foreground_delegation_on_macos_is_recorded(
    tmp_path: Path,
) -> None:
    with running(tmp_path, tui_turns) as (run, server):
        sigkill_on_macos(run, tui_run(run, server, move=False), label="L3b (macOS, foreground)")


# ── win32: the handles the Windows legs read through ─────────────────────


class Win32Kernel:
    """The kernel32 calls :class:`WinHandles` makes, through ctypes; win32 only. Tests pass a fake."""

    SYNCHRONIZE = 0x0010_0000
    QUERY_LIMITED = 0x1000
    TERMINATE = 0x0001
    WAIT_OBJECT_0 = 0
    SNAPPROCESS = 0x2

    _k: Any
    _ctypes: Any

    def __init__(self) -> None:
        if sys.platform != "win32":  # pragma: no cover - the win32 leg only
            raise AssertionError("Win32Kernel is win32 only")
        import ctypes

        handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, argtypes, restype in (
            ("OpenProcess", [dword, boolean, dword], handle),
            ("WaitForSingleObject", [handle, dword], dword),
            ("GetExitCodeProcess", [handle, ctypes.POINTER(dword)], boolean),
            ("TerminateProcess", [handle, ctypes.c_uint], boolean),
            ("GetProcessTimes", [handle, *([ctypes.POINTER(ctypes.c_uint64)] * 4)], boolean),
            ("IsProcessInJob", [handle, handle, ctypes.POINTER(boolean)], boolean),
            (
                "QueryFullProcessImageNameW",
                [handle, dword, ctypes.c_wchar_p, ctypes.POINTER(dword)],
                boolean,
            ),
            ("CreateToolhelp32Snapshot", [dword, dword], handle),
            ("Process32FirstW", [handle, ctypes.c_void_p], boolean),
            ("Process32NextW", [handle, ctypes.c_void_p], boolean),
            ("CloseHandle", [handle], boolean),
        ):
            function = getattr(k, name)
            function.argtypes, function.restype = argtypes, restype
        self._k, self._ctypes = k, ctypes

    def _error(self) -> int:
        return int(self._ctypes.get_last_error())

    def open(self, pid: int) -> int:
        handle = self._k.OpenProcess(
            self.SYNCHRONIZE | self.QUERY_LIMITED | self.TERMINATE, False, pid
        )
        if not handle:
            raise OSError(f"OpenProcess(pid {pid}) failed: error {self._error()}")
        return int(handle)

    def created(self, handle: int) -> float | None:
        times = [self._ctypes.c_uint64() for _ in range(4)]
        if not self._k.GetProcessTimes(handle, *(self._ctypes.byref(t) for t in times)):
            return None
        return float(times[0].value)

    def in_any_job(self, handle: int) -> bool | None:
        answer = self._ctypes.c_int()
        if not self._k.IsProcessInJob(handle, None, self._ctypes.byref(answer)):
            return None
        return bool(answer.value)

    def image(self, handle: int) -> str | None:
        size = self._ctypes.c_uint32(1024)
        buf = self._ctypes.create_unicode_buffer(1024)
        if not self._k.QueryFullProcessImageNameW(handle, 0, buf, self._ctypes.byref(size)):
            return None
        return str(buf.value)

    def parents(self) -> dict[int, int]:
        """Every process's parent pid, from one toolhelp snapshot; empty when it cannot be taken."""

        ctypes = self._ctypes

        class Entry(ctypes.Structure):
            _fields_ = (
                ("dwSize", ctypes.c_uint32),
                ("cntUsage", ctypes.c_uint32),
                ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260),
            )

        snapshot = self._k.CreateToolhelp32Snapshot(self.SNAPPROCESS, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return {}
        found: dict[int, int] = {}
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            more = self._k.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                more = self._k.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self._k.CloseHandle(snapshot)
        return found

    def signalled(self, handle: int) -> bool:
        return bool(self._k.WaitForSingleObject(handle, 0) == self.WAIT_OBJECT_0)

    def exit_code(self, handle: int) -> int | None:
        code = self._ctypes.c_uint32()
        if not self._k.GetExitCodeProcess(handle, self._ctypes.byref(code)):
            return None
        return int(code.value)

    def terminate(self, handle: int) -> tuple[bool, int]:
        ok = bool(self._k.TerminateProcess(handle, 1))
        return ok, 0 if ok else self._error()

    def close(self, handle: int) -> None:
        self._k.CloseHandle(handle)


class WinHandles:
    """win32: one handle per announced process, opened BEFORE the kill; no Job handle is held.

    Opened with ``SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION`` to watch it,
    and ``PROCESS_TERMINATE`` to end the parent and clean up. A held handle keeps
    the pid from being reused, and the creation time read through it must be
    known and the announced one, so each handle is the announced process (Codex
    round 1, L4). A construction that fails closes what it opened (L5).
    """

    def __init__(self, procs: dict[str, Announced], *, kernel: Any = None) -> None:
        self.k = kernel if kernel is not None else Win32Kernel()
        self.procs = procs
        self.handles: dict[str, int] = {}
        self.before: dict[str, dict[str, Any]] = {}
        try:
            parents = self.k.parents()
            for role, p in procs.items():
                handle = self.k.open(p.pid)
                self.handles[role] = handle
                self.before[role] = {
                    "pid": p.pid,
                    "parent_pid": parents.get(p.pid),
                    "image": self.k.image(handle),
                    "in_any_job": self.k.in_any_job(handle),
                    "created": self.k.created(handle),
                }
            unknown_or_moved = {
                r: (b["created"], procs[r].started)
                for r, b in self.before.items()
                if b["created"] is None or b["created"] != procs[r].started
            }
            assert not unknown_or_moved, (
                f"a handle is not known to be the announced process (created now, announced): {unknown_or_moved}"
            )
        except BaseException:
            self._close()
            raise

    def terminate_and_watch(self, target: str, seconds: float) -> dict[str, Any]:
        """``TerminateProcess`` ``target``, then each handle until signalled or ``seconds``: what each did."""

        ended_before = [role for role, handle in self.handles.items() if self.k.signalled(handle)]
        since = time.monotonic()
        returned, error = self.k.terminate(self.handles[target])
        ended: dict[str, float] = {}
        while True:
            for role, handle in self.handles.items():
                if role not in ended and self.k.signalled(handle):
                    ended[role] = round(time.monotonic() - since, 3)
            if len(ended) == len(self.handles) or time.monotonic() - since >= seconds:
                break
            time.sleep(0.05)
        return {
            "terminate": {"target": target, "returned": returned, "last_error": error},
            "ended_before_the_kill": ended_before,
            "processes": {
                role: {
                    **self.before[role],
                    "ended_after": ended.get(role),
                    "exit_code": self.k.exit_code(handle),
                }
                for role, handle in self.handles.items()
            },
        }

    def _close(self) -> None:
        for handle in self.handles.values():
            self.k.close(handle)
        self.handles = {}

    def __enter__(self) -> WinHandles:
        return self

    def __exit__(self, *_exc: Any) -> None:
        for handle in self.handles.values():
            if not self.k.signalled(handle):
                self.k.terminate(handle)
        self._close()
