"""One live writer per session file (#137, ADR-0244).

The defect this exists for is **not** torn bytes. ``append_entry`` reparents
every new entry onto a **process-local** ``_current_leaf_id``
(``session/jsonl_storage.py:692``), so a second process appending to the same
file hangs its turn off a leaf the first process has already moved past. Every
line on disk is valid JSON, every append is atomic, and one terminal's whole
turn is invisible on the next load. A byte-range lock on the session file's own
descriptor would change nothing.

What has to become impossible is the second **writer**. This module is the
mechanism for that and nothing else: it knows how to own a file and how to let
go of it. Who is asked what, and in what words, lives in the product band
(``aelix-coding-agent``) — AGENTS.md §1.

Three facts were measured in the ``filelock`` wheel rather than read from its
README, and each one shapes the code below.

**The lock is a sidecar, never the ``.jsonl`` itself.** On Windows filelock
uses ``LockFileEx`` (``_windows.py``), whose byte-range lock is *mandatory*:
locking the session file would make a read-only viewer's ``read()`` of byte 0
fail there and nowhere else. ``<session>.jsonl.lock`` does not end in
``.jsonl``, so neither ``find_most_recent`` nor ``list`` can see it
(``session/jsonl_repo.py``).

**The lock file is not removed on release on POSIX, and must not be.**
``UnixFileLock`` says so in its own docstring — "We leave the lock file in
place after release. Unlinking a locked file on Unix splits waiters across
inodes and breaks mutual exclusion for processes that coordinate via the same
path." Only ``_windows.py`` unlinks. So a session directory accumulates one
empty ``*.jsonl.lock`` per session on macOS/Linux. That is upstream's
deliberate choice, it is the correct one, and we do not "clean up" after it.

**We do not pass ``fallback_to_soft=``.** filelock degrades a native lock to a
stale-able ``SoftFileLock`` when the filesystem answers ``ENOSYS`` (NFS without
lockd, some FUSE mounts), and the keyword that turns that off only exists from
filelock 3.32.3 (2026-08-13) — a floor that would force an upgrade on any
environment already holding an older filelock through virtualenv, tox or
huggingface_hub, and a ``TypeError`` on the first session open in any
environment that resolved below it. The degradation is detected *afterwards*
instead, by asking what class the lock turned into, which cannot be broken by
a keyword rename. A degraded lock is not a lock: we hold nothing, we say so,
and the caller warns.

That last check is necessary but **not sufficient across the declared floor**,
and review caught the gap. The ``SoftFileLock`` fallback it asks about only
exists from 3.25.2; ``filelock>=3.12,<5`` also accepts 3.12–3.20, and those
raise ``NotImplementedError`` from ``_unix.py`` instead — not an ``OSError``,
so it escaped every arm here. Measured with ``fcntl.flock`` forced to answer
``ENOSYS``: 3.12.0 / 3.16.1 / 3.20.0 raise, 3.25.2 / 3.29.0 / 4.0.1 degrade.
:meth:`SessionWriterLock.try_acquire` therefore catches all three doors, and
none of them is the door a *broken sidecar* comes through.

No heartbeat, no lease, no stale reclamation. Both backends are kernel-held
(``fcntl.flock`` / ``LockFileEx``), so a ``kill -9``, a crashed terminal and a
closed lid all release them. pi carries a ``stale``/``update`` heartbeat
(``experimental/session-worker.ts``) because Node has no OS lock API; Python
has one, so that complexity is not ported.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
from typing import Any

from filelock import FileLock, SoftFileLock, Timeout

_log = logging.getLogger(__name__)

#: The ``errno`` values that mean *this filesystem cannot answer the ownership
#: question*, as opposed to *this particular sidecar is unusable*.
#:
#: Measured, not guessed. ``ENOSYS`` is what ``flock`` answers on NFS without
#: lockd and on some FUSE mounts, and it is the only code filelock itself
#: special-cases. The other three are how a real ``flock`` says the same thing
#: on mounts filelock does not know about: ``ENOLCK`` (no locks available),
#: ``EOPNOTSUPP``/``ENOTSUP`` (the mount does not implement it) and ``EINVAL``
#: (some SMB/9p servers). Everything else — ``EACCES`` and ``EPERM`` on a
#: sidecar this user cannot write, ``ELOOP`` from filelock's ``O_NOFOLLOW``,
#: ``EROFS`` — is a broken lock file, not a filesystem without locking, and
#: answering those with "nobody else has it" would be fail-OPEN against a live
#: owner. That is the one outcome this module exists to make impossible.
_NO_LOCKING_ERRNOS: frozenset[int] = frozenset(
    code
    for code in (
        getattr(errno, _name, None)
        for _name in ("ENOSYS", "ENOLCK", "EOPNOTSUPP", "ENOTSUP", "EINVAL")
    )
    if code is not None
)

#: Appended to the session path to name its sidecar. Deliberately keeps the
#: full ``.jsonl`` in the middle so the sidecar sorts next to its session and
#: is obviously derived from it, while the file itself does not end in
#: ``.jsonl`` and is therefore invisible to the repo's globs.
LOCK_SUFFIX = ".lock"

#: How long ``try_acquire`` waits before calling a file owned.
#:
#: Not a single non-blocking attempt: a quarter second absorbs the legitimate
#: handoff where the other terminal is running ``/resume`` or ``/new`` at that
#: instant, and on Windows it also covers the gap between ``UnlockFileEx`` and
#: the holder's post-release ``unlink``. It is imperceptible at startup and it
#: removes a class of wrong prompt.
DEFAULT_ACQUIRE_TIMEOUT = 0.25


def lock_path_for(session_path: str) -> str:
    """The sidecar path that owns ``session_path``.

    Pure string derivation on purpose — a caller can name the sidecar without
    constructing a lock, which is what the repo-glob tests assert against.
    """

    return f"{session_path}{LOCK_SUFFIX}"


def resolve_session_path(session_path: str) -> str:
    """The canonical spelling of ``session_path``, for ownership purposes.

    Ownership is a property of the **file**, not of the string that named it.
    ``--session`` hands us whatever the user typed, ``_header_to_metadata``
    carries that spelling into ``meta.path`` unchanged, and it arrives here —
    so without this a symlink and its target get one sidecar each. Measured:
    ``alias.jsonl -> a.jsonl`` produced ``alias.jsonl.lock`` and ``a.jsonl.lock``
    and **both** :class:`SessionWriterLock` objects reported ``held=True``,
    which is two terminals appending to one ``.jsonl`` — #137 with the guard
    switched on. Hard links behave the same way.

    ``realpath`` of a path that does not exist yet is still well defined (it
    resolves what it can and appends the rest), which matters because the
    sidecar names a file that may be about to be created.
    """

    try:
        return os.path.realpath(session_path)
    except OSError:  # pragma: no cover — defensive; realpath rarely raises
        return session_path


class SessionWriterLock:
    """Exclusive ownership of one session file, for the life of this process.

    Not re-entrant across files: one instance owns one path. Moving a live
    process from one session to another is ``release()`` on the old instance
    and ``try_acquire()`` on a new one — see
    ``AgentSessionRuntime._take_writer_lock``.
    """

    __slots__ = (
        "_session_path",
        "_resolved_path",
        "_lock_path",
        "_timeout",
        "_lock",
        "_held",
        "_degraded",
        "_error",
    )

    def __init__(
        self, session_path: str, *, timeout: float = DEFAULT_ACQUIRE_TIMEOUT
    ) -> None:
        self._session_path = session_path
        self._resolved_path = resolve_session_path(session_path)
        # Derived from the RESOLVED path, so two spellings of one file
        # contend on one sidecar — see :func:`resolve_session_path`.
        self._lock_path = lock_path_for(self._resolved_path)
        self._timeout = timeout
        self._lock: Any | None = None
        self._held = False
        self._degraded = False
        self._error: OSError | None = None

    @property
    def session_path(self) -> str:
        """The path this lock was asked for, in the caller's own spelling.

        What the user gets told about. :attr:`resolved_path` is what the
        kernel is asked about.
        """

        return self._session_path

    @property
    def resolved_path(self) -> str:
        """The canonical path whose ownership this lock actually represents."""

        return self._resolved_path

    @property
    def lock_path(self) -> str:
        return self._lock_path

    @property
    def held(self) -> bool:
        """True once this process owns the file through a kernel lock.

        False while degraded — :attr:`degraded` is the honest answer that we
        are writing without having proved we are alone.
        """

        return self._held

    @property
    def degraded(self) -> bool:
        """True when this filesystem cannot answer the ownership question.

        The caller should say so once, on stderr, and carry on: refusing to
        start would lock a user out of their own sessions over a mount option,
        and taking a stale-able soft lock would hand them a file nobody can
        open until they delete it by hand.
        """

        return self._degraded

    @property
    def error(self) -> OSError | None:
        """Why the sidecar itself could not be used, when that is the answer.

        Set only when :meth:`try_acquire` returned ``False`` for a reason that
        is **not** contention: an unwritable ``<session>.jsonl.lock``, a
        symlinked one, a read-only mount. Callers that would otherwise say
        "already open in another terminal" read this first, because that
        sentence would be a guess and the real ``OSError`` is actionable.
        """

        return self._error

    def try_acquire(self) -> bool:
        """Take the writer lock. ``False`` means we do not own the file.

        Idempotent: calling it again while held is a no-op that returns True.
        A degraded lock also returns True — see :attr:`degraded`.

        ``False`` has two shapes, and :attr:`error` tells them apart: ``None``
        is contention (another live process owns it), anything else is a
        sidecar this process cannot use at all.
        """

        if self._held or self._degraded:
            return True
        self._error = None

        lock = FileLock(
            self._lock_path,
            timeout=self._timeout,
            # The owner of a session file is the PROCESS, not the thread that
            # happened to open it. With filelock's default (``True``) the whole
            # lock context is thread-local, so a release from a different
            # thread than the acquire — an ``asyncio.to_thread`` teardown, a
            # signal handler — would see an unheld lock and silently leave the
            # file owned for the rest of the process's life.
            thread_local=False,
        )
        try:
            lock.acquire()
        except Timeout:
            if isinstance(lock, SoftFileLock):
                # The filesystem has no ``flock`` and filelock fell back to an
                # ``O_CREAT|O_EXCL`` marker, which outlives the process that
                # made it. Losing that race is not evidence of a live writer —
                # it is just as likely a marker left by a crash — so it must
                # not become "already open in another terminal".
                return self._degrade("this filesystem has no file locking")
            return False
        except NotImplementedError as exc:
            # The door an older filelock comes through, and the reason this
            # arm is not folded into the ``OSError`` one: it is not an
            # ``OSError``. Every filelock from the declared floor (3.12)
            # through 3.20 turns a ``flock`` ``ENOSYS`` into
            # ``NotImplementedError("FileSystem does not appear to support
            # flock")`` instead of degrading; the ``SoftFileLock`` fallback
            # the two ``isinstance`` arms rely on first appears in 3.25.2.
            # Measured against ``fcntl.flock`` raising ``ENOSYS``: 3.12.0,
            # 3.16.1 and 3.20.0 raise, 3.25.2 / 3.29.0 / 4.0.1 degrade.
            # Before this arm it escaped ``try_acquire``, escaped
            # ``_resolve_session_ownership`` and reached ``main_sync``'s bare
            # ``raise`` — so a user whose environment already pinned an older
            # filelock (torch, huggingface_hub, virtualenv and tox all pull
            # one) and whose sessions root is on NFS or 9p could not start
            # aelix at all, where the design promises one line on stderr and
            # a degraded run.
            return self._degrade(f"this filesystem has no file locking ({exc})")
        except OSError as exc:
            if exc.errno in _NO_LOCKING_ERRNOS:
                # A mount that cannot lock. Same verdict as the two arms
                # above, arrived at by a third door.
                return self._degrade(f"file locking is unavailable here ({exc})")
            # NOT a filesystem without locking: a sidecar this process cannot
            # use. ``sudo aelix`` leaves a root-owned ``<session>.jsonl.lock``
            # behind, a shared sessions directory crosses accounts, filelock
            # opens with ``O_NOFOLLOW`` so a symlinked sidecar is ``ELOOP``.
            # Degrading here would tell the user their filesystem is at fault
            # and then hand them the file while its real owner is still
            # appending to it.
            self._error = exc
            # ``debug``, not ``warning`` — unlike ``_degrade``, this outcome is
            # never silent: it is returned on :attr:`error` and both callers
            # surface it (the CLI prints the sidecar's path and the OSError,
            # the runtime raises ``SessionError``). Measured at warning it
            # printed the same sentence to stderr twice.
            _log.debug(
                "session writer lock unavailable for %s: %s",
                self._session_path,
                exc,
            )
            return False

        if isinstance(lock, SoftFileLock):
            # It *did* take a lock, but a stale-able one. Drop it rather than
            # leave a marker that a crash turns into a file nobody can open.
            with contextlib.suppress(Exception):
                lock.release()
            return self._degrade("this filesystem has no file locking")

        self._lock = lock
        self._held = True
        return True

    def _degrade(self, why: str) -> bool:
        self._degraded = True
        _log.warning("session writer lock degraded for %s: %s", self._session_path, why)
        return True

    def release(self) -> None:
        """Let go of the file. Safe to call when not held, and twice.

        On POSIX the sidecar stays on disk; see the module docstring. Nothing
        here unlinks it, because unlinking a path other processes coordinate
        on is how mutual exclusion is lost.
        """

        lock, self._lock = self._lock, None
        self._held = False
        if lock is None:
            return
        try:
            lock.release()
        except Exception:  # noqa: BLE001 — teardown must not raise
            _log.exception("releasing the session writer lock raised")

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        state = "degraded" if self._degraded else ("held" if self._held else "free")
        return f"SessionWriterLock({self._session_path!r}, {state})"


__all__ = [
    "DEFAULT_ACQUIRE_TIMEOUT",
    "LOCK_SUFFIX",
    "SessionWriterLock",
    "lock_path_for",
    "resolve_session_path",
]
