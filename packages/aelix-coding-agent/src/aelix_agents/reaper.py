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

A reaper ALREADY in its grace is escalated through its ``escalate`` event, not
through a flag: it read ``eager_kill`` when it started, so setting
``row.eager_kill`` afterwards changed nothing it would read again. Until #691
a ``stop`` that reused that reaper, or a second cancel, left it to wait out
the grace it had started — measured, ``abort_child`` returned only when the
grace did (4.70 s of a 5 s one through a print run), and with ``_reap``
driven directly a ``setsid`` descendant holding the child's stdout outlived
the second cancel by the whole grace (1.45 s of 1.5 s). A print reaper's one
grace is the post-EOF bound's, which needs both pipes at EOF, so that
descendant cannot be holding one there: an Esc in it finds the root alive and
``_eager_abort`` ends what it walks at once, and the event is what makes the
reaper run Q1 now for a member of the child's group the walk did not name
(measured: dead 0.020 s after the Esc, and alive 7 s later without the event,
its root dead by then). The dead-root shape is ``RpcChannel._shutdown``'s, a
channel the bundled runtime does not build (ADR-0291 §6). ``_eager_abort`` and
``abort_child`` set the row's event, and :func:`reap` waits on it beside the
grace.

WHY A DESCENDANT WALK AND NOT ``os.killpg`` (finding I2). An earlier draft
justified the group kill with *"the child's own ``bash`` tool children must die
too"*. That is factually wrong: ``tools/bash.py:362`` and
``tools/_subprocess.py:107`` both spawn through
``containment_spawn_kwargs(new_session=True)`` — which is the
``start_new_session=True`` those two lines used to spell literally, and still is
on POSIX — so a grandchild is the leader of its OWN group and ``killpg`` on the
child's group cannot reach it. The descendant walk can. It reads ``/proc`` on
Linux and asks the kernel through libproc on macOS (#691, ADR-0291).

On the COOPERATIVE SIGTERM leg the walk is redundant — the child's own
``_signal_cleanup_and_exit`` → ``dispose()`` → ``abort()`` → ``bash.py:576-591``
already reaps its grandchildren. That last hop was a ``_kill_group(proc.pid)``
until #222 and is the bash tool's own ``ProcessTree`` now, so on Windows it
reaches a grandchild whose intermediate parent already exited (a job does;
``taskkill /T`` follows live parent links only). On POSIX it is still a group
kill and still cannot name a ``setsid`` grandchild, which is why nothing below
changed. The escalation leg exists precisely for the child that does NOT
cooperate, which is also the child whose grandchildren nobody else will clean
up.

AND EVERY STOP A HUMAN DRIVES IS THE ESCALATION LEG (#691). ``stop``, ``stop_all``
and the extension teardown reach :func:`reap` with ``eager_kill=True``, a SIGTERM
and on the same synchronous run a SIGKILL; Esc, Esc Esc and a signal to a
headless parent reach ``PrintChannel._eager_abort``, a SIGKILL alone. So the
child's ``dispose()`` gets no time to end the command its ``bash`` tool is
running — it can START, on a parent descheduled between the SIGTERM and the
SIGKILL (measured with a 20 ms pause), and nothing here depends on whether it
does — and the walk is the only thing that names that command. Until #691 the
walk read ``/proc`` alone, so on macOS it was empty and that command was left
running with PPID 1 on every one of those paths (measured, the TUI and ``-p``).
The walk now asks the kernel there too — ``proc_listpids(PROC_PPID_ONLY, …)``,
one call per process in the tree — and keeps every property below: it is taken
before any signal, from a child that has not been reaped, and deepest first. Why
the walk and not a SIGTERM first that lets the child clean up: ADR-0291.

AND THE GROUPS THE WALK'S PROCESSES LEAD (#691). The ``bash`` tool's shell leads
its own group, and what it backgrounded stays there after its parent exits:
``(sleep X &); sleep Y`` leaves X with PPID 1, which no walk can name, in the
shell's group. So every walked process that leads its own group
(:func:`group_leaders`, read with the walk) gets one ``killpg(SIGKILL)`` after
the pids (:func:`kill_tree`'s ``groups``). A group holds only processes of its
leader's session, and every session below the walk was started inside the
delegated child's tree, so the group kill reaches nothing that tree did not
start; the pid it is addressed to is ADR-0238's recycling residual, as every
pid here is. It also takes what a shell started after the walk, which is most of
ADR-0291's limit 1 (measured with a fork loop there, §5).

ON WINDOWS THERE IS NEITHER (#220). ``descendant_pids`` returns ``[]`` there, with
no ``/proc`` to read and no libproc to ask, and ``os.kill`` delivers no catchable
signal, so both legs are
handed to the ``ProcessTree`` the spawn site attached — a Job Object, which holds
every descendant including one that made itself a session leader. The win32 arms
of :func:`reap` and :func:`kill_tree` therefore do not consult the walk at all —
:func:`reap` takes none on its tree arm, and :func:`kill_tree`'s ignores the one
it is handed (empty there anyway); they call ``soft_kill()``
(``CTRL_BREAK_EVENT``) and ``hard_kill()`` (``taskkill /T /F`` then
``TerminateJobObject``) and return.

AND ONE GROUP KILL IS ADDED ON POSIX — a supplement, never a substitution (#220,
Q1). :func:`reap`'s escalation calls ``tree.hard_kill()`` (``killpg(pgid,
SIGKILL)``) *after* the descendant loop, so the group only ever addresses what the
walk could not name; the walk stays the thing that reaches a ``setsid``
grandchild, which ``killpg`` cannot. It was added because the walk was EMPTY on
macOS: measured on Darwin, a non-``setsid`` descendant of a SIGTERM-ignoring
child survived the root-only escalation and died to the group kill
(``.omc/specs/220-progress-2026-09-05.md`` §2). Since #691 the walk names that
descendant on macOS too, and Q1 is what it always said it was: the leg for a
group member the walk did NOT name — one the child spawned after the snapshot
(on the grace leg the snapshot precedes the SIGTERM and a SIGTERM-ignoring child
keeps working for the whole grace), or a walk that failed. The in-tree guard
blinds the walk to model exactly that
(``tests/agents_ext/test_print_channel_spawn.py::test_a_non_setsid_grandchild_dies_on_the_escalation``,
which times out with the grandchild alive when this line is deleted). It lives
in :func:`reap` ONLY — :func:`kill_tree` says why.

PID-RECYCLING HAZARD, and it is why the snapshot is taken where it is. The
event loop's child watcher may already have ``waitpid``'d the child by the time
the escalation runs, so re-resolving anything from ``proc.pid`` at that moment
can name a RECYCLED, unrelated process. ``contextlib.suppress(ProcessLookupError)``
does not protect against that — the pid exists, it is just somebody else's.
Hence the descendant walk is snapshotted BEFORE the SIGTERM, while the child is
provably alive — and the groups its processes lead with it, never re-read once a
signal has gone out — and the child itself is signalled through :func:`_signal_child`
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
import ctypes
import functools
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aelix_ai.utils import _process_walk as _walk
from aelix_ai.utils._process_tree import wait_released

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    # Typed here only: at RUNTIME this module touches exactly three members of a
    # tree — ``closed``, ``soft_kill()`` and ``hard_kill()`` — so a test can hand
    # ``reap`` a three-method stub and no ctypes/``_KernelApi`` is ever built.
    # ``aelix_agents`` imports the private module directly, as ``rpc/rpc_client.py``
    # and ``extensions/subprocess_hooks.py`` already do. There is no other route:
    # the ``aelix_coding_agent/tools/_process_tree.py`` shim that re-exported
    # ``kill_process_tree`` alone was deleted in #222, when the two tool spawn
    # sites it existed for stopped calling that function and took a tree instead.
    from aelix_ai.utils._process_tree import ProcessTree

DEFAULT_GRACE_SECONDS = 5.0
"""SIGTERM → SIGKILL grace. Long enough for the child's own
``_signal_cleanup_and_exit`` to flush its session, dispose its harness and kill
its bash grandchildren; short enough that a wedged child does not hold a
``/agents run`` panel for a noticeable time."""

_PR_SET_PDEATHSIG = 1

_PROC = Path("/proc")

_LIBPROC_PATH = _walk.LIBPROC_PATH
"""Where macOS exports ``proc_listpids``. There is no such file on disk since
macOS 11; ``dlopen`` resolves the path from the dyld shared cache, which is how
``ctypes.util.find_library("proc")`` answers it too."""

_PROC_PPID_ONLY = _walk.PROC_PPID_ONLY
"""``proc_listpids``'s filter from ``<sys/proc_info.h>``: the processes whose
parent is the ``typeinfo`` argument."""

_FIRST_CAPACITY = _walk.FIRST_CAPACITY
"""Pids :func:`_darwin_children` asks for first. The kernel fills the buffer and
drops the rest without saying so, so a full answer is asked again at twice the
size."""

_MAX_CAPACITY = _walk.MAX_CAPACITY
"""Where that doubling stops: above any ``kern.maxproc``, so an answer this large
is the whole answer."""


def descendant_pids(pid: int) -> list[int]:
    """Every live descendant of ``pid``, DEEPEST FIRST.

    Deepest-first so a grandchild is signalled before its parent — killing the
    parent first can leave the grandchild reparented to init and outside any
    later walk.

    TWO SOURCES, ONE WALK. On Linux it reads ``/proc/<pid>/status`` (the ``PPid:``
    line) rather than ``/proc/<pid>/stat``: ``stat``'s second field is the
    executable name in parentheses and may itself contain spaces AND parentheses,
    so field-index parsing of ``stat`` is wrong for any process that chooses a
    hostile name. On macOS, which has no ``/proc``, it asks the kernel for each
    process's children with ``proc_listpids(PROC_PPID_ONLY, …)`` (#691) — no
    process names are read at all. Before #691 the walk was empty there, and a
    delegated child's ``bash`` command outlived every stop that SIGKILLs the child
    at once (the module docstring). ``/proc`` is tested first, so a Linux host
    walks ``/proc`` whatever ``sys.platform`` a test injects.

    Neither source is one snapshot. The ``/proc`` walk reads one file per process
    and the macOS walk makes one call per process in the tree, so a process forked
    between two reads can be missed — the race ADR-0197 accepts for the walk.

    Returns ``[]`` on Windows, which has neither source, and on any read error. A
    best-effort answer is correct here: the child itself is always signalled
    through :func:`_signal_child`, so an empty walk degrades to "kill the child
    only", never to "kill nothing".
    """

    # #722 — the walk lives in ``aelix_ai.utils._process_walk`` now (the job
    # service reads it too); this module's names stay the seams its tests patch.
    return _walk.descendant_pids(pid, proc=_PROC, is_darwin=_is_darwin, libproc=_libproc)


def _deepest_first(pid: int, children_of: Callable[[int], Iterable[int]]) -> list[int]:
    """Breadth-first from ``pid``, then reverse.

    BFS visits by generation, so the reversed order is deepest-generation first.
    ``seen`` guards against the cycle a pid-reuse race can synthesise in the
    snapshot.
    """

    return _walk.deepest_first(pid, children_of)


def _is_darwin() -> bool:
    """``sys.platform``, read at CALL time so a test can inject it (:func:`_is_win32`)."""

    return sys.platform == "darwin"


@functools.cache
def _libproc() -> ctypes.CDLL | None:
    """libproc with ``proc_listpids`` typed, or ``None`` when it cannot be loaded.

    Loaded on first use and only on macOS, so no other platform ever touches it.
    ``None`` makes the walk empty, which is the answer it gave before #691.
    """

    return _walk.load_libproc()


def _darwin_children(libproc: Any, pid: int) -> list[int]:
    """``pid``'s children, from ``proc_listpids(PROC_PPID_ONLY, pid, …)``.

    The call answers in BYTES and ``0`` on failure, an unknown pid included. It
    fills at most the buffer and says nothing about the rest, so an answer that
    fills it is asked again at twice the size. A zombie child is listed too;
    signalling one is harmless.
    """

    return _walk.darwin_children(libproc, pid)


def _proc_children() -> dict[int, list[int]]:
    """The ``/proc`` ``PPid`` index: parent pid → its children's pids."""

    return _walk.proc_children(_PROC)


def group_leaders(pids: Iterable[int]) -> list[int]:
    """Those of ``pids`` that lead their own process group, read NOW (#691).

    The other half of the walk's snapshot, taken right after it: before any
    signal, while every pid in it was just seen alive. :func:`kill_tree` sends
    each of these groups one ``SIGKILL`` after the pids, for what a group holds
    that the walk cannot name — ``(cmd &)`` leaves ``cmd`` with PPID 1 in the
    group of the ``bash`` tool's shell, and a command that shell starts after
    the walk is there too. Only while that shell runs: a shell that has exited
    is not walked, so what it left in its group — ``cmd &``, ``(cmd &)`` or
    ``nohup cmd &`` from a ``bash`` call that returned — is named here by
    nothing (ADR-0291 limit 2).

    WHY IT CANNOT REACH A STRANGER. A process can join only a group of its own
    session, and no process can join a session it was not born in. The delegated
    child leads a session of its own (``start_new_session=True``), so every
    session below the walk was started inside the child's tree, and so is every
    member of every group named here. What is left is the number itself: a
    leader that exits and is reaped after this read, in a group whose members
    have all gone too, frees it, and a new process that took it AND made itself
    a group leader before the kill would be signalled with its group. That is
    ADR-0238's recycling residual with a group in place of a pid, and the reason
    this is never re-read once a signal has gone out (ADR-0291).

    ``getpgid`` failing is "not a leader": a pid that exited since the walk
    (``ESRCH``; Darwin answers that for a zombie too) has no group to kill
    through it. ``[]`` on Windows, where the job is the tree.
    """

    return _walk.group_leaders(pids, platform=sys.platform)


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
    ``descendant_pids`` returns ``[]`` on Windows — the
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
    proc: Any,
    descendants: Sequence[int] = (),
    *,
    tree: ProcessTree | None = None,
    groups: Sequence[int] = (),
) -> None:
    """SIGKILL ``descendants`` (deepest first), the child itself, then ``groups``.

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
    callers' walks are empty on Windows anyway, ``descendant_pids`` having
    nothing to read there. The arm is deliberately not followed by
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

    ``groups`` (#691) is not that kill, and it is the callers' too. It is
    :func:`group_leaders` of the same snapshot as ``descendants`` — the groups
    the WALKED processes lead, never the child's own — and each gets one
    ``killpg(SIGKILL)`` after every pid, for the members the walk could not
    name. The caller reads it with the walk, because on the grace leg this runs
    seconds after the snapshot and a pgid read now may be a stranger's.
    ``_drain_after_exit`` passes none: its pids come from a pipe scan of a child
    that is already dead, on the success path, where a group kill is the
    revision-1 ``close()`` again.
    """

    if _is_win32() and _usable(tree):
        tree.hard_kill()  # pyright: ignore[reportOptionalMemberAccess]
        return
    sig = _kill_signal()
    for pid in descendants:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, sig)
    _signal_child(proc, sig)
    if sys.platform != "win32":
        # After every pid, so a group addresses only what the walk could not
        # name. ``ESRCH`` is the common answer: the walk named every member.
        for pgid in groups:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pgid, signal.SIGKILL)


def kill_group_if_live(proc: Any, tree: ProcessTree | None) -> bool:
    """Q1's group kill for a SYNCHRONOUS caller whose child may still be alive.

    :func:`reap` runs Q1 inline because it knows it is escalating. This is the
    same leg for the one other place that has to supply it — the second-Ctrl+C
    / last-line-of-defence path in ``PrintChannel._eager_abort``, which without
    it reaches only what the walk named, the groups those processes lead, and
    the root: a member of the CHILD's own group that the walk missed survives
    it, exactly the hole Q1 exists to close (#220 review round 2, posix2/P2;
    measured before #691, when the macOS walk was empty: a delegation cancelled
    with no reaper task left its non-``setsid`` grandchild alive on Darwin).

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
    groups: Sequence[int] | None = None,
    tree: ProcessTree | None = None,
    escalate: asyncio.Event | None = None,
    owner: Any | None = None,
) -> int:
    """SIGTERM → grace → SIGKILL. Safe to be cancelled REPEATEDLY.

    Returns the child's exit status. ``eager_kill=True`` skips the grace: it is
    what a second Ctrl+C means — the human has already waited once and pressing
    again is a request to stop waiting, not to restart the clock.

    ``escalate`` is the same request for a reaper that is ALREADY in its grace
    (#691): set — by ``_eager_abort`` on a second cancel, or by ``abort_child``
    reusing this task for a ``stop`` — it ends the grace at once and the
    escalation runs, with the snapshot taken before the SIGTERM. Set before this
    starts, it is ``eager_kill``. A flag could not do this: the running
    coroutine read ``eager_kill`` once, on entry.

    ``descendants`` is the pre-``terminate()`` snapshot from
    :func:`descendant_pids`, and ``groups`` the :func:`group_leaders` read with
    it. Passing them in lets the caller take the snapshot while the child is
    provably alive (see the module docstring on pid recycling); ``descendants=None``
    means "snapshot both now", which is correct for every caller that reaches
    this function before signalling anything. A list handed in with no
    ``groups`` kills no group: the groups belong to the read that produced the
    pids, and are not looked up later from them. On the win32 tree arm there is
    no walk to take — the job is the descendant set — so none is (#691).

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
      walk and the groups its processes lead), plus Q1: one ``tree.hard_kill()``
      — ``killpg(pgid, SIGKILL)`` on the CHILD's group — AFTER the walk, for a
      group member the walk did not name: one spawned after the snapshot, or a
      walk that failed (module docstring). Until #691 the walk was ``[]`` on
      macOS and every non-``setsid`` descendant was one.

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

    if escalate is not None and escalate.is_set():
        eager_kill = True
    if owner is not None:
        return await _reap_owned(proc, owner, grace=grace, eager_kill=eager_kill, escalate=escalate)
    if descendants is not None:
        snapshot = list(descendants)
        leaders = list(groups) if groups is not None else []
    elif _is_win32() and _usable(tree):
        # The job is the descendant set; :func:`kill_tree`'s tree arm would
        # ignore a walk, so none is taken (#691).
        snapshot, leaders = [], []
    else:
        snapshot = descendant_pids(proc.pid)
        leaders = group_leaders(snapshot)
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
        # The grace ends when the TREE has let go of the child's pipes, not when
        # the root exits (#192 review round 5): from CPython 3.13.15 ``waiter``
        # resolves at the root's exit (gh-119710), and a child that dies on the
        # SIGTERM while a pipe-holding descendant lives on then ended the grace
        # at once and skipped the escalation below - the descendant survived.
        # :func:`~aelix_ai.utils._process_tree.wait_released` is what ``waiter``
        # meant up to 3.13.14. Its own ``proc.wait()`` is a second waiter, which
        # is legal; ``waiter`` stays the one the final reap awaits.
        #
        # Raced against ``escalate`` too (#691): a second cancel or a ``stop``
        # ends the grace here instead of after it. ``asyncio.wait`` cancels
        # neither on its way out, so the ``finally`` does.
        released = asyncio.ensure_future(wait_released(proc))
        escalated = (
            asyncio.ensure_future(escalate.wait()) if escalate is not None else None
        )
        try:
            await asyncio.wait(
                {released} if escalated is None else {released, escalated},
                timeout=grace,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if released.done():
                # The tree let go first (or with the request): nothing is left
                # to escalate against. A ``released`` that raised re-raises
                # here and escalates below, as the timeout does.
                return int(released.result())
        except BaseException:  # noqa: BLE001 — CancelledError, and a failed release
            # Deliberately NOT ``Exception``. A ``CancelledError`` here is the
            # second Ctrl+C (finding B1); swallowing it and escalating is the
            # ONLY behaviour that does not leave a session leader alive. The
            # cancellation is not lost — the caller awaits this task under
            # ``asyncio.shield`` and re-raises on its own side.
            pass
        finally:
            released.cancel()
            if escalated is not None:
                escalated.cancel()

    kill_tree(proc, snapshot, tree=tree, groups=leaders)
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


async def _reap_owned(
    proc: Any,
    owner: Any,
    *,
    grace: float,
    eager_kill: bool,
    escalate: asyncio.Event | None,
) -> int:
    """:func:`reap` for a SERVICE-OWNED row (#611 S1c, ADR-0295): the owner kills, this waits.

    A background delegation is a job of its session, and the job service's
    barrier confirms its processes gone from the evidence its own legs collect
    BEFORE their first signal (ADR-0293 §5). This reaper's legs would signal
    first and walk after — and on win32 its tree arm empties the Job's member
    list before anything has waited on a member — so for such a row it signals
    nothing itself: the owner's soft leg, its grace and its hard leg
    (``owner.stop``), or its hard leg at once (``owner.stop_now``) when the stop
    is eager or escalated mid-grace. Then the one ``proc.wait()`` this function
    always ends with, shielded, so the child is reaped and no zombie is left.
    """

    waiter = asyncio.ensure_future(proc.wait())
    if eager_kill:
        owner.stop_now()
    else:
        soft = asyncio.ensure_future(owner.stop(grace))
        # A strong reference until it ends: a second cancel leaves this frame
        # with the legs still running, and the loop keeps only a weak one.
        _OWNED_STOPS.add(soft)
        soft.add_done_callback(_OWNED_STOPS.discard)
        escalated = asyncio.ensure_future(escalate.wait()) if escalate is not None else None
        try:
            await asyncio.wait(
                {soft} if escalated is None else {soft, escalated},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not soft.done():
                # Escalated mid-grace: the hard leg now. The soft legs finish on
                # their own (their hard leg finds nothing left); this waits for the
                # process, not for them.
                owner.stop_now()
        except BaseException:  # noqa: BLE001 — a second cancel: the hard leg now
            # As :func:`reap` does: escalating is the one answer that leaves no
            # session leader alive, and the caller re-raises its own cancel.
            owner.stop_now()
        finally:
            if escalated is not None:
                escalated.cancel()
    return int(await asyncio.shield(waiter))


_OWNED_STOPS: set[asyncio.Future[Any]] = set()


def _resolve_prctl() -> Callable[..., Any] | None:
    """``prctl`` from libc, loaded and looked up in THIS process, or ``None``.

    Called by :func:`pdeathsig_preexec` in the spawning aelix, before the fork,
    so the child only calls a function that is already resolved. Building the
    ``CDLL`` and looking the symbol up used to happen in the child, between
    ``fork`` and ``exec``, where a lock another parent thread held at the fork
    is held for ever (Codex review of #721, finding 7; already so on main
    ``fc8f3048``). ``None`` off Linux, and wherever libc cannot be loaded
    (musl has no ``libc.so.6``): the hook then arms nothing, as it always did
    there.
    """

    if sys.platform != "linux":
        return None
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True).prctl
    except Exception:  # noqa: BLE001 - best effort, as the arming always was
        return None


def pdeathsig(
    parent_pid: int | None = None, prctl: Callable[..., Any] | None = None
) -> None:
    """``preexec_fn`` that asks the kernel to SIGTERM us when our parent dies.

    Closes finding I1. Without it a child whose parent is SIGKILLed runs to
    completion, reparented to init: ``modes/print_mode.py:338-345``'s ``_emit``
    only RECORDS ``stdout_dead["v"]`` when the pipe breaks, and the acting
    ``break`` (``:385-390``) plus the ``raise BrokenPipeError`` (``:399-402``)
    are both strictly AFTER ``await runtime_host.harness.prompt(initial_message)``
    (``:366-370``). Since ``agents/resolver.py:621-622`` makes the whole task the
    initial prompt, the EPIPE guard covers nothing a subagent does.

    Composes with ``start_new_session=True``: ``setsid()`` does not clear the
    pdeathsig, and neither does ``exec`` (only a UID change does). Linux-only —
    ``PR_SET_PDEATHSIG`` has no portable equivalent, and the parent-death race is
    accepted elsewhere.

    ``prctl`` is libc's, resolved by :func:`pdeathsig_preexec` before the fork
    (:func:`_resolve_prctl`); without it nothing is armed, because loading it
    here is the thing a child between ``fork`` and ``exec`` must not do.

    SIGTERM GOES BACK TO ITS DEFAULT FIRST (Codex review of #721, finding 6).
    The fork inherits the parent's handler — the TUI and print mode install one,
    an asyncio one on POSIX — so a SIGTERM that reached the child before
    ``exec`` only tripped that handler, and ``exec`` then ran: a parent dying
    after the recheck below was swallowed, and so was the reaper's own SIGTERM
    in that window. With the default restored the signal ends the child where
    it stands. ``exec`` resets a caught signal to its default anyway, so the
    command starts exactly as it did. An ignored SIGTERM is left ignored, as
    ``exec`` would leave it.

    THE RECHECK (#611 S1a, Codex r5 finding 13). ``PR_SET_PDEATHSIG`` watches the
    parent the child has WHEN IT IS ARMED. A parent that died between ``fork``
    and this call has already handed the child to another process — init, or a
    subreaper whose pid is not 1 — and the signal is then armed against that one,
    so the child runs on under it. ``parent_pid`` is the spawning aelix's pid,
    read before the fork (:func:`pdeathsig_preexec`); after arming, a
    ``getppid()`` that differs from it means that parent is gone, and the child
    exits before ``exec``. Compared with the pid itself, not with 1, which a
    subreaper would pass. codex-rs does the same
    (``utils/pty/src/process_group.rs:25-37``, ``set_parent_death_signal``).

    It exits with ``os._exit(128 + SIGTERM)`` where codex-rs raises ``SIGTERM``,
    because a raise does not end a forked Python child whose parent handled
    SIGTERM: the TUI and print mode install a handler (an asyncio one on
    POSIX), the fork inherits it, the raise only trips it, and ``exec`` then
    runs. Measured on macOS 3.12 with a ``loop.add_signal_handler(SIGTERM)``
    parent: a raising hook let ``sh -c 'echo EXECED'`` exit 0 printing
    ``EXECED``; ``os._exit`` ended it with 143 before ``exec``.

    Runs between ``fork`` and ``exec``, so it must not allocate, log or raise:
    a bare ``except`` is correct here in a way it almost never is. The recheck
    runs even when arming failed — a parent that is already gone is the same
    fact either way. What is left is CPython's own: ``preexec_fn`` is Python
    code, and the ``signal.signal`` and ``prctl`` calls still run the
    interpreter in the child, which Python documents as unsafe with threads in
    the parent. No library is loaded and no symbol looked up here any more.
    """

    if sys.platform != "linux":  # pragma: no cover - platform guard
        return
    try:
        if callable(signal.getsignal(signal.SIGTERM)):
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except Exception:  # noqa: BLE001 - pragma: no cover - best effort, pre-exec
        pass
    if prctl is not None:
        try:  # noqa: SIM105 - not ``suppress``: no context manager between fork and exec
            prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        except Exception:  # noqa: BLE001 - pragma: no cover - best effort, pre-exec
            pass
    if parent_pid is not None and os.getppid() != parent_pid:
        os._exit(128 + signal.SIGTERM)


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
    patching ``sys.platform``, per this repo's no-``skipif`` convention — and,
    since #611 S1a, because it is where the parent's pid is read: called in the
    spawning process right before the spawn, it binds ``os.getpid()`` into the
    hook as the ``parent_pid`` :func:`pdeathsig` rechecks after arming, and the
    already-resolved ``prctl`` (:func:`_resolve_prctl`), so the child loads no
    library.

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

    if sys.platform == "win32":
        return None
    return functools.partial(pdeathsig, os.getpid(), _resolve_prctl())


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "descendant_pids",
    "group_leaders",
    "kill_group_if_live",
    "kill_tree",
    "pdeathsig",
    "pdeathsig_preexec",
    "reap",
]
