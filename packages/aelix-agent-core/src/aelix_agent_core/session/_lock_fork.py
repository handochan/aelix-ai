"""Fence fork against transfers of the kernel lock and proof descriptors."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import weakref
from collections.abc import Callable, Iterator
from typing import Any

_gate = threading.Condition()
_transitions = 0
_local = threading.local()
_locks: weakref.WeakKeyDictionary[Any, tuple[int, int] | None] = weakref.WeakKeyDictionary()
_sockets: set[socket.socket] = set()
_child_reset: Callable[[], None] | None = None


@contextlib.contextmanager
def transition() -> Iterator[None]:
    global _transitions
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    try:
        with _gate:
            _transitions += 1
        try:
            yield
        finally:
            with _gate:
                _transitions -= 1
                if not _transitions:
                    _gate.notify_all()
    finally:
        _local.depth = depth


def _fd_identity(fd: int) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino
    return None


def track_lock(lock: Any, fd: int) -> None:
    if _HAS_FORK:
        _locks[lock] = _fd_identity(fd)


def untrack_lock(lock: Any) -> None:
    _locks.pop(lock, None)


def track_socket(sock: socket.socket) -> socket.socket:
    _sockets.add(sock)
    return sock


def close_socket(sock: socket.socket) -> None:
    with transition():
        _sockets.discard(sock)
        sock.close()


def set_child_reset(reset: Callable[[], None]) -> None:
    global _child_reset
    _child_reset = reset


def _reject_reentrant_fork(event: str, _args: tuple[object, ...]) -> None:
    if event in ("os.fork", "os.forkpty") and getattr(_local, "depth", 0):
        # The at-fork wait would be waiting on this same thread's transition.
        raise RuntimeError("cannot fork during a session-lock transition")


def _before_fork() -> None:
    _gate.acquire()
    _gate.wait_for(lambda: _transitions == 0)


def _after_parent() -> None:
    _gate.release()


def _after_child() -> None:
    global _gate, _locks
    # filelock is imported before this hook is installed. Versions with
    # their own child hook run first and may already have detached the fd.
    for lock, expected in list(_locks.items()):
        context = lock._context
        fd = context.lock_file_fd
        if fd is not None:
            context.lock_file_fd = None
            context.lock_counter = 0
            if expected is not None and _fd_identity(fd) == expected:
                with contextlib.suppress(OSError):
                    os.close(fd)  # close only; LOCK_UN would unlock the parent's fd
    _locks = weakref.WeakKeyDictionary()
    for sock in _sockets:
        with contextlib.suppress(OSError):
            sock.close()
    _sockets.clear()
    _gate = threading.Condition()
    if _child_reset is not None:
        _child_reset()


_HAS_FORK = hasattr(os, "register_at_fork")
if os.name == "posix" and _HAS_FORK:
    sys.addaudithook(_reject_reentrant_fork)
    os.register_at_fork(before=_before_fork, after_in_parent=_after_parent, after_in_child=_after_child)
