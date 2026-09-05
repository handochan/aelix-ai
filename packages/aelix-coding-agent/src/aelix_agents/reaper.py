"""The cancellation-safe kill path — ADR-0197 §(j), review findings B1 and I2.

No argv, no pipes, no envelope: this module knows only how to make a child and
its descendants stop existing, and how to survive being cancelled while doing
it. :mod:`aelix_agents.print_channel` owns everything else.

WHY THIS IS NOT A THREE-LINE ``terminate(); await wait()`` (finding B1). The
parent's running-turn interrupt has **no debounce**: ``tui/chrome.py::_interrupt``
(``:854``) reaches its running branch at ``:863-866`` — ``if self._running: …
on_interrupt(); return`` — on EVERY Ctrl+C, and Esc is bound to the same callback
at ``:883-890`` under ``Condition(lambda: self._running)``. The
``_CTRL_C_EXIT_WINDOW`` debounce (the constant at ``:159``) is read only at
``:875``, inside the IDLE, empty-buffer branch at ``:872-882``, so it never
applies here. N presses are therefore N ``cancel()`` calls on the same turn
task, roughly 0.4 s apart — exactly what an impatient human produces.

A plain coroutine reaper takes that second ``CancelledError`` at its
``wait_for`` during the 5 s grace and NEVER reaches the SIGKILL leg. The child
was started with ``start_new_session=True`` (``print_channel.py``), so it is a
session leader: no terminal signal reaches it, the parent-side timeout is gone
with the cancelled task, and it runs every remaining turn, every LLM call and
every tool to completion — reparented to init, still holding the API keys.

Two defences, and BOTH are required:

* :func:`reap` catches ``BaseException``, not ``Exception``, so a
  ``CancelledError`` escalates exactly like a timeout does;
* the caller runs it as a DETACHED task and awaits it under
  :func:`asyncio.shield`, so cancelling the awaiter never cancels the kill
  (``print_channel.PrintChannel.run``), and re-cancelling escalates EAGERLY
  rather than waiting out a grace nobody is left to enforce.

WHY A ``/proc`` WALK AND NOT ``os.killpg`` (finding I2). An earlier draft
justified the group kill with *"the child's own ``bash`` tool children must die
too"*. That is factually wrong: ``tools/bash.py:278`` uses
``subprocess.Popen(..., start_new_session=True)`` and ``tools/_subprocess.py:78``
uses ``create_subprocess_exec(..., start_new_session=True)``, so a grandchild is
the leader of its OWN group and ``killpg`` on the child's group cannot reach it.
The descendant walk can.

On the COOPERATIVE SIGTERM leg the walk is redundant — the child's own
``_signal_cleanup_and_exit`` → ``dispose()`` → ``abort()`` →
``bash.py:325-330`` ``_kill_group`` already reaps its grandchildren. The
escalation leg exists precisely for the child that does NOT cooperate, which is
also the child whose grandchildren nobody else will clean up.

ON WINDOWS THERE IS NEITHER (#220). ``descendant_pids`` returns ``[]`` with no
``/proc`` to read and ``os.kill`` delivers no catchable signal, so both legs are
handed to the ``ProcessTree`` the spawn site attached — a Job Object, which holds
every descendant including one that made itself a session leader. The win32 arms
of :func:`reap` and :func:`kill_tree` therefore do not consult the walk at all
(``descendants`` is empty there anyway); they call ``soft_kill()``
(``CTRL_BREAK_EVENT``) and ``hard_kill()`` (``taskkill /T /F`` then
``TerminateJobObject``) and return.

AND ONE GROUP KILL IS ADDED ON POSIX — a supplement, never a substitution (#220,
Q1). :func:`reap`'s escalation calls ``tree.hard_kill()`` (``killpg(pgid,
SIGKILL)``) *after* the descendant loop, so the group only ever addresses what the
walk could not name; the walk stays the thing that reaches a ``setsid``
grandchild, which ``killpg`` cannot. It exists because the walk is EMPTY on
macOS: measured on Darwin, a non-``setsid`` descendant of a SIGTERM-ignoring
child survives today's root-only escalation and dies to the group kill
(the in-tree guard is
``tests/agents_ext/test_print_channel_spawn.py::test_a_non_setsid_grandchild_dies_on_the_escalation``,
which times out with the grandchild alive when this line is deleted; the live
``main``-side half is ``.omc/specs/220-progress-2026-09-05.md`` §2). It lives in
:func:`reap` ONLY — :func:`kill_tree` says why.

PID-RECYCLING HAZARD, and it is why the snapshot is taken where it is. The
event loop's child watcher may already have ``waitpid``'d the child by the time
the escalation runs, so re-resolving anything from ``proc.pid`` at that moment
can name a RECYCLED, unrelated process. ``contextlib.suppress(ProcessLookupError)``
does not protect against that — the pid exists, it is just somebody else's.
Hence the descendant walk is snapshotted BEFORE the SIGTERM, while the child is
provably alive, and the child itself is signalled through :func:`_signal_child`
— which re-reads ``proc.returncode`` immediately before the ``os.kill``, with no
``await`` in between — rather than through a re-resolved pgid.

AND NEVER THROUGH ``proc.terminate()`` / ``proc.kill()``, which reap behind the
event loop's back and cost the real exit status. That is :func:`_signal_child`'s
whole reason to exist; the measurement is in its docstring (MEDIUM #4).

DO NOT PORT PI'S ESCALATION LITERALLY. Node's ``subprocess.killed`` means "a
signal was sent", so pi's ``if (!proc.killed) proc.kill("SIGKILL")``
(``index.ts:403-407``) is false five seconds later in every case and the SIGKILL
is never sent. The Python predicate must be LIVENESS (``returncode is None``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    # Typed here only: at RUNTIME this module touches exactly three members of a
    # tree — ``closed``, ``soft_kill()`` and ``hard_kill()`` — so a test can hand
    # ``reap`` a three-method stub and no ctypes/``_KernelApi`` is ever built.
    # ``aelix_agents`` imports the private module directly, as ``rpc/rpc_client.py``
    # and ``extensions/subprocess_hooks.py`` already do; the
    # ``aelix_coding_agent/tools/_process_tree.py`` shim is not a route, because it
    # re-exports ``kill_process_tree`` alone.
    from aelix_ai.utils._process_tree import ProcessTree

DEFAULT_GRACE_SECONDS = 5.0
"""SIGTERM → SIGKILL grace. Long enough for the child's own
``_signal_cleanup_and_exit`` to flush its session, dispose its harness and kill
its bash grandchildren; short enough that a wedged child does not hold a
``/agents run`` panel for a noticeable time."""

_PR_SET_PDEATHSIG = 1

_PROC = Path("/proc")


def descendant_pids(pid: int) -> list[int]:
    """Every live descendant of ``pid``, DEEPEST FIRST.

    Deepest-first so a grandchild is signalled before its parent — killing the
    parent first can leave the grandchild reparented to init and outside any
    later walk.

    Reads ``/proc/<pid>/status`` (the ``PPid:`` line) rather than
    ``/proc/<pid>/stat``: ``stat``'s second field is the executable name in
    parentheses and may itself contain spaces AND parentheses, so field-index
    parsing of ``stat`` is wrong for any process that chooses a hostile name.

    Returns ``[]`` on a platform without ``/proc`` (macOS, Windows) and on any
    read error. A best-effort answer is correct here: the child itself is always
    signalled through ``proc.kill()``, so an empty walk degrades to "kill the
    child only", never to "kill nothing".
    """

    if not _PROC.is_dir():
        return []
    parents: dict[int, int] = {}
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            # The process exited between the listing and the read — normal.
            continue
        for line in status.splitlines():
            if line.startswith("PPid:"):
                with contextlib.suppress(ValueError):
                    parents[int(name)] = int(line.split()[1])
                break

    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)

    # Breadth-first from ``pid``, then reverse: BFS visits by generation, so the
    # reversed order is deepest-generation first. ``seen`` guards against the
    # cycle a pid-reuse race can synthesise in the snapshot.
    ordered: list[int] = []
    seen: set[int] = {pid}
    frontier = list(children.get(pid, ()))
    while frontier:
        nxt: list[int] = []
        for candidate in frontier:
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
            nxt.extend(children.get(candidate, ()))
        frontier = nxt
    ordered.reverse()
    return ordered


def _signal_child(proc: Any, sig: int) -> None:
    """Send ``sig`` to ``proc`` WITHOUT letting ``subprocess`` reap it.

    NEVER ``proc.terminate()`` / ``proc.kill()`` (P2 review, MEDIUM #4).
    ``asyncio.subprocess.Process.terminate`` reaches ``subprocess.Popen.
    send_signal``, whose FIRST statement is ``self.poll()`` — a
    ``waitpid(pid, WNOHANG)``. If the child has already exited but the event
    loop's watcher has not yet delivered the status, that ``poll()`` reaps the
    zombie out from under the loop; the watcher's own ``waitpid`` then raises
    ``ChildProcessError`` and asyncio substitutes 255
    (``unix_events.PidfdChildWatcher._do_wait``, and the threaded watcher does
    the same). Measured over 60 reaps of a child that really exited 0 with the
    loop briefly blocked: ``{255: 60}``, one
    ``"child process pid … exit status already read: will report returncode
    255"`` warning apiece. ``envelope.build_result`` computes
    ``failed = … or exit_code != 0``, so on any leg whose outcome is not already
    a failure — ``stop()`` / ``stop_all()`` racing a child that just finished,
    which ``abort_child`` always drives with ``eager_kill=True`` — that turned a
    SUCCESSFUL delegation into ``ok=False, status="error", exit_code=255``.

    ``os.kill`` has no such side effect: signalling a zombie is a no-op that
    leaves the status for the watcher to collect, so ``await proc.wait()``
    still yields the real exit code.

    PID RECYCLING is NARROWED by the ``returncode is None`` re-read, not
    eliminated, and the difference is measured. asyncio's child watcher calls
    ``waitpid`` on its OWN THREAD and only a later loop callback copies the
    status into ``returncode``, so "no ``await`` in between" stops no coroutine
    from reaping and ``returncode is None`` does not prove the pid is still
    held (``_process_tree.py``'s "THE PID/PGID HAZARD, BOUNDED PER SITE";
    measured there — a loop blocked 1.5 s still read ``None`` for a released
    pid). What the re-read buys is the window between the top-of-function guard
    and this line; the residual is the same recycling hazard the module
    docstring names for the descendant snapshot, accepted in ADR-0238.
    """

    if getattr(proc, "returncode", None) is not None:
        return
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int):  # pragma: no cover — a stub without a pid
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, sig)


def _is_win32() -> bool:
    """``sys.platform``, read at CALL time so a test can inject it.

    A function rather than a module constant for the same reason
    :func:`_kill_signal` reads it late: the win32 reaper cases patch
    ``reaper.sys.platform`` (``tests/agents_ext/test_reaper_kill_signal_win32.py``,
    ``tests/agents_ext/test_reaper_tree_win32.py``), which is this repo's
    inject-don't-skip convention for a platform branch.
    """

    return sys.platform == "win32"


def _usable(tree: ProcessTree | None) -> bool:
    """A tree that can still kill — not merely a tree that is present.

    Both legs of a CLOSED tree early-return (``soft_kill`` → ``False``,
    ``hard_kill`` → no-op), so dispatching to one signals nothing at all. A
    closed tree is USUALLY an already-dead tree — closing the last handle of a
    ``kill_on_close=True`` job terminates its members (ADR-0238) — but the
    ``contained=False, job=None`` fallback closes without killing anything, and
    falling back to the signal legs is strictly safer than dispatching into a
    disarmed object.

    ``tree.closed`` is read as an ATTRIBUTE, deliberately not
    ``getattr(tree, "closed", False)``: a recording stub that forgets to declare
    it must fail loudly instead of silently taking the POSIX arm.
    """

    return tree is not None and not tree.closed


def _kill_signal() -> int:
    """The signal that terminates unconditionally on THIS platform.

    ``signal.SIGKILL`` does not exist on Windows, and :func:`kill_tree` named
    it unguarded. ``AttributeError`` is no subclass of ``OSError``, so the
    ``contextlib.suppress`` around the ``os.kill`` did not catch it either and
    it escaped the handler whose entire job is to make a child stop existing.
    The first ``windows-latest`` run died in :func:`kill_tree`'s descendant
    loop, on ``AttributeError: module 'signal' has no attribute 'SIGKILL'``
    (#103).

    ``SIGTERM`` there is NOT a softening, and that is the only thing that makes
    the substitution honest. Windows ``os.kill`` does not deliver signals at
    all: every value except ``CTRL_C_EVENT`` and ``CTRL_BREAK_EVENT`` reaches
    ``TerminateProcess(handle, sig)``, which the target cannot catch, block or
    handle. The escalation is as absolute there as SIGKILL is here.

    WHAT WAS MISSING ON WINDOWS was one step earlier, and no signal choice here
    could reach it. :func:`reap`'s FIRST leg was ``os.kill`` too, so the
    *cooperative* SIGTERM was that same uncatchable ``TerminateProcess``: the
    child never ran its ``_signal_cleanup_and_exit``, the grace bought nothing,
    and since that cleanup is what reaps its ``bash`` grandchildren —
    ``descendant_pids`` returns ``[]`` with no ``/proc`` to walk — the
    grandchildren were orphaned by the FIRST signal, before this function was
    ever consulted. Closing it needed process-group or job-object isolation at
    the SPAWN site, which Windows silently declines for ``start_new_session``
    (CPython names the parameter ``unused_start_new_session``). #202 built the
    primitive that Windows does not decline — ``aelix_ai.utils._process_tree``,
    a Job Object there and a process group here — and **#220 adopted it**: the
    print channel spawns with ``containment_spawn_kwargs`` and attaches a tree,
    and :func:`reap`'s win32 leg 1 is that tree's ``CTRL_BREAK_EVENT``, which
    the child CAN handle.

    So this function is now the WINDOWS FALLBACK, not the windows path: it is
    reached there only when no usable tree arrived (attach failed and was
    degraded to ``tree=None``, or the tree was already closed). Its answer is
    unchanged and still has to be right, because that fallback is exactly the
    case in which nothing else will end the child.

    Decided on ``sys.platform`` rather than ``hasattr(signal, "SIGKILL")``,
    matching ``session/fs.py`` and :func:`pdeathsig_preexec`: pyright narrows
    it, which also retires the ``reportAttributeAccessIssue`` the two lines
    below raised on the windows type gate, and it keeps a platform decision
    looking like one instead of like an inference from a missing name.
    """

    if sys.platform == "win32":
        return signal.SIGTERM
    return signal.SIGKILL


def kill_tree(
    proc: Any, descendants: Sequence[int] = (), *, tree: ProcessTree | None = None
) -> None:
    """SIGKILL ``descendants`` (deepest first) and then the child itself.

    Synchronous and total: every signal is best-effort and no failure
    propagates. ``ProcessLookupError`` is the normal case (the target already
    died), ``PermissionError`` means a pid was recycled into another user's
    process — in both cases the only correct response is to move on to the next
    pid.

    Does NOT wait. The caller must still await the process, or the exit status
    is never collected and a zombie leaks (see :func:`reap`).

    The signal comes from :func:`_kill_signal` rather than being the literal
    ``signal.SIGKILL`` it reads as: that name does not exist on Windows, and
    naming it here is what made this function raise there.

    ``tree`` (#220) replaces the whole body on win32, and ONLY there. The job IS
    the descendant set, so ``descendants`` is ignored on that arm — both product
    callers' walks are empty on Windows anyway, ``descendant_pids`` having no
    ``/proc`` to read. The arm is deliberately not followed by
    :func:`_signal_child`: on Windows that is ``TerminateProcess(handle, sig)``,
    which would race ``TerminateJobObject(job, 1)`` for the exit status the
    escalation tests assert.

    NO POSIX ``tree.hard_kill()`` HERE, and that omission is load-bearing —
    but the reason is a PER-CALLER one, not a property every caller shares.
    ``print_channel._drain_after_exit`` reaches this function on the delegation
    SUCCESS path, entered only after ``exited.result()``, so its child is
    already dead and already reaped; a ``killpg(SIGKILL)`` from THERE would fire
    at a group whose leader has been reaped, which is revision 1 of
    ``ProcessTree.close()`` again: it killed helpers a hook had deliberately
    backgrounded and exited 0 over (``_process_tree.py``'s "``close()`` IS A
    RELEASE, NOT A KILL"). The other two callers — ``PrintChannel._eager_abort``
    and ``RpcChannel._eager_abort`` — reach it with a child that is usually very
    much ALIVE (the ``except asyncio.CancelledError`` on the first Ctrl+C, and
    the last-line-of-defence ``except Exception``), and only sometimes with a
    dead one (a cancel that landed inside ``_drain_after_exit``). A blanket
    group kill in this function cannot tell those apart; the caller can, by
    re-reading ``returncode`` on the line of the call, and
    ``PrintChannel._eager_abort`` does exactly that (#220 review round 2,
    posix2/P2). So the group kill is dispatched by CALLERS that hold a bound,
    never from here.
    """

    if _is_win32() and _usable(tree):
        tree.hard_kill()  # pyright: ignore[reportOptionalMemberAccess]
        return
    sig = _kill_signal()
    for pid in descendants:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)
    _signal_child(proc, sig)


def kill_group_if_live(proc: Any, tree: ProcessTree | None) -> bool:
    """Q1's group kill for a SYNCHRONOUS caller whose child may still be alive.

    :func:`reap` runs Q1 inline because it knows it is escalating. This is the
    same leg for the one other place that has to supply it — the second-Ctrl+C
    / last-line-of-defence path in ``PrintChannel._eager_abort``, which on
    macOS is otherwise a root-only ``SIGKILL`` with an empty descendant walk
    beside it, exactly the hole Q1 exists to close (#220 review round 2,
    posix2/P2; measured: a delegation cancelled with no reaper task left its
    non-``setsid`` grandchild alive on Darwin).

    THREE conditions, and each one is load-bearing:

    * **not win32** — there ``kill_tree``'s tree arm already ran
      ``hard_kill()``; a second one would be a second ``taskkill.exe`` spawn on
      the event-loop thread for nothing.
    * **usable tree** — a closed tree's ``hard_kill`` is a no-op, and
      ``contained`` is what stops ``killpg`` addressing a group the spawn did
      not create (``ProcessTree.hard_kill``).
    * **``returncode is None``** — THE discriminator, and the reason this is a
      caller-side leg rather than a line in :func:`kill_tree`. A cancellation
      that lands inside ``_drain_after_exit`` reaches ``_eager_abort`` with a
      child that is already dead and already reaped, and a ``killpg(SIGKILL)``
      there is revision 1 of ``ProcessTree.close()`` again. It is read on the
      line of the call with no ``await`` in between, and the caller is
      synchronous, so nothing can run to change it. It NARROWS the window
      rather than closing it — see :func:`_signal_child` and
      ``_process_tree.py``'s "THE PID/PGID HAZARD" for why, and for the
      residual ADR-0238 accepts.

    :func:`reap`'s own Q1 deliberately does NOT take the liveness guard: after
    the grace the leader is usually dead and a descendant is not, which is the
    shape the group kill exists for.

    Returns whether the group kill was dispatched, so a test can assert the
    negative without reading a private flag.
    """

    if _is_win32() or not _usable(tree):
        return False
    if getattr(proc, "returncode", None) is not None:
        return False
    tree.hard_kill()  # pyright: ignore[reportOptionalMemberAccess]
    return True


async def reap(
    proc: Any,
    *,
    grace: float = DEFAULT_GRACE_SECONDS,
    eager_kill: bool = False,
    descendants: Sequence[int] | None = None,
    tree: ProcessTree | None = None,
) -> int:
    """SIGTERM → grace → SIGKILL. Safe to be cancelled REPEATEDLY.

    Returns the child's exit status. ``eager_kill=True`` skips the grace: it is
    what a second Ctrl+C means — the human has already waited once and pressing
    again is a request to stop waiting, not to restart the clock.

    ``descendants`` is the pre-``terminate()`` snapshot from
    :func:`descendant_pids`. Passing it in lets the caller take the snapshot
    while the child is provably alive (see the module docstring on pid
    recycling); ``None`` means "snapshot now", which is correct for every caller
    that reaches this function before signalling anything.

    THE FINAL ``await`` IS NOT OPTIONAL. Without it the child becomes a zombie:
    SIGKILL removes the process but not its exit status, and only a ``wait``
    releases the entry. It is shielded so that a cancellation arriving between
    the kill and the wait cannot skip it.

    ``tree`` (#220) is the containment the spawn site attached, and it changes
    both legs on win32 and exactly one thing on POSIX:

    * **win32, usable tree** — leg 1 is ``tree.soft_kill()``, a
      ``CTRL_BREAK_EVENT`` the child's own ``SIGBREAK`` handler can run cleanup
      for; ``os.kill`` there could only be an uncatchable ``TerminateProcess``.
      ``soft_kill()`` returning ``False`` means nothing was delivered (no
      console, ``ESRCH``, closed), so there is nothing to wait for and the grace
      is skipped — the same decision ``RpcClient.stop()`` makes. Escalation is
      :func:`kill_tree`'s job arm.
    * **win32, no usable tree** — today's sequence, byte for byte. That fallback
      is why :func:`_kill_signal` still matters.
    * **POSIX** — legs unchanged (``SIGTERM`` to the root, then the descendant
      walk), plus Q1: one ``tree.hard_kill()`` — ``killpg(pgid, SIGKILL)`` —
      AFTER the walk, because the walk is ``[]`` on macOS and a non-``setsid``
      descendant survives without it (measured; module docstring).

    ``eager_kill=True`` on the win32 tree arm does NOT send the console event
    (Q4): ``abort_child`` always passes it, so this is the common ``stop`` /
    ``stop_all`` path, and with a zero grace the event would only start a
    ``dispose()`` that ``hard_kill`` truncates microseconds later — while
    doubling the latency of a leg that already blocks the loop on a
    ``taskkill.exe`` spawn. The POSIX arm keeps its SIGTERM-then-SIGKILL
    sequence; the divergence is win32-cost-driven and deliberate.
    """

    returncode = getattr(proc, "returncode", None)
    if returncode is not None:
        return int(returncode)

    snapshot = (
        list(descendants) if descendants is not None else descendant_pids(proc.pid)
    )
    # ONE waiter, reused by both legs. Two concurrent ``proc.wait()`` calls are
    # legal, but a fresh coroutine per leg makes the "was it already awaited"
    # reasoning harder than it needs to be.
    waiter = asyncio.ensure_future(proc.wait())

    # ``os.kill``, never ``proc.terminate()`` — see :func:`_signal_child` for the
    # measured 255 this avoids (MEDIUM #4). ``_signal_child`` re-reads
    # ``returncode`` itself, and there is deliberately no ``await`` between that
    # read and the signal.
    if _is_win32() and _usable(tree):
        # Written as ONE short-circuiting condition rather than nested ``if`` statements
        # (ruff SIM102), and the order of its three clauses is the contract:
        # ``eager_kill`` first, so Q4's "no console event on the abort path"
        # holds; then ``returncode`` re-read on the line of the call with no
        # ``await`` between, exactly as :func:`_signal_child` does it — the
        # guard three statements above is a coincidence of today's layout, not
        # a contract, and a console event addressed to a group whose leader has
        # been reaped is a signal to somebody else. No test pins this re-read
        # and none can while that layout holds: nothing between the two reads
        # yields, so they cannot observe different values. It is defensive
        # against a future ``await`` being inserted above it (#220 review round
        # 2, mutation MUT-6 — an equivalent mutant, recorded rather than
        # chased).
        if (
            not eager_kill
            and getattr(proc, "returncode", None) is None
            and not tree.soft_kill()  # pyright: ignore[reportOptionalMemberAccess]
        ):
            # Nothing was delivered, so there is nothing to wait for.
            eager_kill = True
    else:
        _signal_child(proc, signal.SIGTERM)

    if not eager_kill:
        try:
            return int(await asyncio.wait_for(asyncio.shield(waiter), grace))
        except BaseException:  # noqa: BLE001 — TimeoutError AND CancelledError
            # Deliberately NOT ``Exception``. A ``CancelledError`` here is the
            # second Ctrl+C (finding B1); swallowing it and escalating is the
            # ONLY behaviour that does not leave a session leader alive. The
            # cancellation is not lost — the caller awaits this task under
            # ``asyncio.shield`` and re-raises on its own side.
            pass

    kill_tree(proc, snapshot, tree=tree)
    if not _is_win32() and _usable(tree):
        # Q1. AFTER the walk, so the group kill addresses only what the walk
        # could not name.
        #
        # WHAT BOUNDS IT is the GROUP, not the pid, and the difference is
        # measured. "``reap`` owns the sole ``proc.wait()`` and has not awaited
        # it, so the child is unreaped" is FALSE on asyncio: the child watcher
        # calls ``waitpid`` on its own thread and a later loop callback copies
        # the status into ``returncode`` (``_process_tree.py``'s "THE PID/PGID
        # HAZARD, BOUNDED PER SITE" — measured: a loop blocked 1.5 s still read
        # ``returncode is None`` for a pid the kernel had released). What holds
        # instead is what holds at the rpc and hook sites: a NON-EMPTY group's
        # id cannot be reused while any member lives (POSIX.1 §3.293), and an
        # empty one answers ``ESRCH``. The residual — the number recycled AND
        # its new holder having made itself a group leader inside that window —
        # is ADR-0238's accepted residual, and this is its third site.
        #
        # Deliberately NOT guarded by ``returncode is None`` here: after the
        # grace the leader is often dead while a descendant is not, and that is
        # the exact shape this exists for (``ProcessTree.hard_kill``'s docstring
        # says the same). ``PrintChannel._eager_abort`` DOES take that guard,
        # because there it is the discriminator between a live child and a
        # cancel that landed on the already-reaped drain path.
        tree.hard_kill()  # pyright: ignore[reportOptionalMemberAccess]
    return int(await asyncio.shield(waiter))


def pdeathsig() -> None:
    """``preexec_fn`` that asks the kernel to SIGTERM us when our parent dies.

    Closes finding I1. Without it a child whose parent is SIGKILLed runs to
    completion, reparented to init: ``modes/print_mode.py:221-228``'s ``_emit``
    only RECORDS ``stdout_dead["v"]`` when the pipe breaks, and the acting
    ``break`` (``:198-205``) plus the ``raise BrokenPipeError`` (``:208-211``)
    are both strictly AFTER ``await runtime_host.harness.prompt(initial_message)``
    (``:189-193``). Since ``agents/resolver.py:314-315`` makes the whole task the
    initial prompt, the EPIPE guard covers nothing a subagent does.

    Composes with ``start_new_session=True``: ``setsid()`` does not clear the
    pdeathsig, and neither does ``exec`` (only a UID change does). Linux-only —
    ``PR_SET_PDEATHSIG`` has no portable equivalent, and the parent-death race is
    accepted elsewhere.

    Runs between ``fork`` and ``exec``, so it must not allocate, log or raise:
    a bare ``except`` is correct here in a way it almost never is.
    """

    if sys.platform != "linux":  # pragma: no cover - platform guard
        return
    try:
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(
            _PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0
        )
    except Exception:  # noqa: BLE001 - pragma: no cover - best effort, pre-exec
        pass


def pdeathsig_preexec() -> Callable[[], None] | None:
    """The ``preexec_fn`` a delegation spawn should pass: the hook, or nothing.

    ``pdeathsig`` is already inert off Linux, so this is not about what the
    hook *does*. It is about whether the argument can be passed at all:
    ``subprocess`` REJECTS a non-None ``preexec_fn`` on Windows before it
    creates anything ("preexec_fn is not supported on Windows platforms",
    CPython ``subprocess.py``). Both delegation channels wrap the spawn in
    ``except Exception`` and turn a failure into an error envelope, so that
    ValueError did not surface as a crash — every delegation simply came back
    "error" and 52 of the 238 remaining ``windows-latest`` failures were
    downstream of it (#200).

    A function rather than a module constant so a case can drive the branch by
    patching ``sys.platform``, per this repo's no-``skipif`` convention.

    Note what this does NOT fix, and who does. The sibling
    ``start_new_session=True`` is accepted on Windows and silently ignored —
    CPython names the parameter ``unused_start_new_session`` there — so the
    process-group isolation both call sites document was absent on Windows
    rather than merely degraded. #202 built the primitive
    (``aelix_ai.utils._process_tree``, a Job Object on Windows) and **#220
    adopted it**: the print channel now spawns with
    ``containment_spawn_kwargs(new_session=True)``, which is
    ``{"start_new_session": True}`` here and ``creationflags=
    CREATE_NEW_PROCESS_GROUP`` there. This hook is still POSIX's alone —
    Windows has no ``pdeathsig``; the job's ``kill_on_close`` is the nearest
    equivalent and fires on the parent's death by a different mechanism
    (ADR-0238).
    """

    return None if sys.platform == "win32" else pdeathsig


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "descendant_pids",
    "kill_group_if_live",
    "kill_tree",
    "pdeathsig",
    "pdeathsig_preexec",
    "reap",
]
