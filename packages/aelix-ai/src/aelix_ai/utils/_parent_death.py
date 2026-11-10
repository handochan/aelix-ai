"""The parent-death hook a delegation spawn passes as ``preexec_fn`` (ADR-0197 finding I1, #721).

Moved down from ``aelix_agents/reaper.py`` in #611 S2a, unchanged, so a spawn
site in ``aelix_coding_agent`` (the background ``bash`` of #611 S2d) can take it
too: product-core may not import ``aelix_agents`` (ADR-0197, the band rule).
``aelix_agents.reaper`` re-exports :func:`pdeathsig` and
:func:`pdeathsig_preexec`, so every caller and every test that reaches them
through it is unchanged.
"""

from __future__ import annotations

import ctypes
import functools
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

_PR_SET_PDEATHSIG = 1


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


__all__ = ["pdeathsig", "pdeathsig_preexec"]
