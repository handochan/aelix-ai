"""``FileSystem`` Protocol + ``LocalFileSystem`` impl (Sprint 4a).

Pi source: ``packages/agent/src/harness/types.ts:273-323`` (``FileSystem``)
+ ``packages/agent/src/harness/env/nodejs.ts``. Aelix simplifies by raising
``OSError`` directly instead of Pi's ``Result<T, FileError>`` ADT (idiomatic
Python). The JSONL boundary wraps these into
:class:`SessionError("storage", ...)` so the public surface stays Pi-shaped.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

FileKind = Literal["file", "directory", "symlink"]

#: Session files are owner-only (Track S2). A session JSONL stores every
#: prompt and every tool result verbatim — an agent that runs ``env`` writes
#: an API key into this file — so it gets the same treatment as
#: ``auth.json`` (see ``aelix_ai.oauth.auth_storage``) rather than the
#: umask default, which on a stock box is world-readable.
SESSION_FILE_MODE = 0o600
SESSION_DIR_MODE = 0o700

#: Every descriptor that writes session bytes is binary (#294, ADR-0242).
#: Without ``O_BINARY`` a Windows descriptor is text-mode and turns each
#: ``\n`` into ``\r\n`` on the way to disk — the stdlib adds the flag by hand
#: for the same reason (``tempfile``, ``_pyio.FileIO``). It does not exist on
#: POSIX, where there is nothing to translate, so it is ``0`` there.
_O_BINARY = getattr(os, "O_BINARY", 0)


def _tighten(path: str | Path, mode: int) -> None:
    """Best-effort ``chmod``, ignoring failures.

    Creation modes only apply to files this process creates; sessions
    written before Track S2 are already on disk at 0644. Callers run this
    on the paths they touch so an existing session tightens on next use.
    A failure here (foreign owner, exotic filesystem) must not break the
    session — the write itself is what matters.
    """

    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte of ``data`` to ``fd``, or raise.

    ``os.write`` may write fewer bytes than it was given (a signal, a full
    disk, a quota) and reports that only in its return value. Ignoring the
    return value dropped the tail of a session line with no exception at
    all — the next load skipped the line and pruned every entry below it
    (#294). A write that makes no progress raises ``EIO`` instead of letting
    the loop spin.
    """

    view = memoryview(data)
    while len(view) > 0:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(
                errno.EIO, f"os.write made no progress with {len(view)} bytes left"
            )
        view = view[written:]


#: Seconds to wait before each retry of a Windows rename refused with
#: ``PermissionError`` — about one second in all.
_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.3, 0.35)


async def _replace(source: str, destination: str) -> None:
    """``os.replace``, retried for about a second on a Windows ``PermissionError``.

    Every session create and every import now ends in a rename (#294), and
    Windows refuses a rename while another process still holds the file: an
    antivirus scanner, the search indexer or a sync client opening the temp
    that was just closed makes ``MoveFileExW`` fail with a sharing violation or
    access denied, both ``PermissionError``. pip retries ``os.replace`` for
    the same reason. Nothing holds a file that way on POSIX, where the error
    is final at once. Only the rename is retried: the temp is complete by
    then, so waiting cannot publish anything partial.
    """

    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if sys.platform != "win32":
                raise
        await asyncio.sleep(delay)
    os.replace(source, destination)


@dataclass(frozen=True)
class FileInfo:
    """Pi ``FileInfo`` (``types.ts:235-246``)."""

    name: str
    path: str
    kind: FileKind
    size: int
    mtime_ms: float


@runtime_checkable
class FileSystem(Protocol):
    """Pi ``FileSystem`` (``types.ts:273-323``).

    Aelix-additive divergence (ADR-0022): methods raise ``OSError`` directly
    rather than returning a ``Result`` ADT. JSONL boundary callers translate
    to :class:`SessionError` via ``try/except OSError`` wrappers.

    ``rename_file`` (pi ``renameFile``, #294) atomically replaces
    ``destination`` with ``source`` within one directory. The JSONL store
    publishes whole files with it — ``write_file`` to ``<path>.tmp``, then
    ``rename_file`` onto ``<path>`` — so a created or forked session appears
    whole or not at all (ADR-0242). A custom implementation must provide it.

    A write (``write_file``, ``append_file``) is over when the call returns
    or raises, cancellation included. An implementation that hands the bytes
    to a worker thread waits for the worker even when it is cancelled
    (shield it) rather than leaving it running: the JSONL store releases its
    lock and re-arms its healing newline as soon as an append raises, so
    bytes that land afterwards end up inside the next line and cost that
    entry on reload (ADR-0242). ``LocalFileSystem`` writes synchronously.
    """

    cwd: str

    async def absolute_path(self, path: str) -> str: ...
    async def join_path(self, parts: list[str]) -> str: ...
    async def read_text_file(self, path: str) -> str: ...
    async def read_text_lines(
        self, path: str, *, max_lines: int | None = None
    ) -> list[str]: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def append_file(self, path: str, content: str) -> None: ...
    async def list_dir(self, path: str) -> list[FileInfo]: ...
    async def exists(self, path: str) -> bool: ...
    async def create_dir(self, path: str, *, recursive: bool = True) -> None: ...
    async def remove(
        self, path: str, *, recursive: bool = False, force: bool = False
    ) -> None: ...
    async def copy_file(self, source: str, destination: str) -> None: ...
    async def rename_file(self, source: str, destination: str) -> None: ...


class LocalFileSystem:
    """Default ``FileSystem`` wrapping ``pathlib`` / ``os``.

    All async methods are thin wrappers; blocking I/O is moved off the event
    loop via ``asyncio.to_thread`` only at the JSONL boundary where
    appropriate (Sprint 4a accepts in-thread blocking for ``list_dir`` /
    ``exists`` because those run rarely).

    Durability is a PROCESS crash, not a power loss (ADR-0242). Whatever an
    append or a publish wrote is on disk if the process dies at any later
    instruction, but nothing is fsynced — the level pi's JSONL store promises
    too. The one exception is :meth:`copy_file`, which can replace an
    existing complete session and so fsyncs its staged copy before renaming
    it. No directory is ever fsynced.
    """

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd or os.getcwd()

    async def absolute_path(self, path: str) -> str:
        return str(Path(path).expanduser().resolve(strict=False))

    async def join_path(self, parts: list[str]) -> str:
        if not parts:
            return ""
        first = parts[0]
        return str(Path(first, *parts[1:]))

    async def read_text_file(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    async def read_text_lines(
        self, path: str, *, max_lines: int | None = None
    ) -> list[str]:
        if max_lines is None:
            with open(path, encoding="utf-8") as f:
                return [line.rstrip("\n") for line in f]
        out: list[str] = []
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                out.append(line.rstrip("\n"))
        return out

    async def write_file(self, path: str, content: str) -> None:
        """Create or truncate ``path`` at 0600 and write every byte of ``content``.

        The staging half of a publish: the JSONL store writes ``<path>.tmp``
        with this, then moves it into place with :meth:`rename_file`, so no
        reader ever opens a partial session. Not fsynced (process-crash
        durability, see the class docstring). An existing file is truncated
        and tightened rather than refused, so a ``.tmp`` a crash left behind
        is simply reused by the next publish to the same path.
        """

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True, mode=SESSION_DIR_MODE)
        _tighten(p.parent, SESSION_DIR_MODE)
        # O_CREAT mode is umask-masked, but umask can only clear bits —
        # 0600 never widens — so creation is safe without a chmod.
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY,
            SESSION_FILE_MODE,
        )
        try:
            self._tighten_if_loose(fd)
            _write_all(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    async def append_file(self, path: str, content: str) -> None:
        """Append every byte of ``content`` to ``path``, creating it at 0600.

        ``O_APPEND``: each ``os.write`` lands at the then-current end of the
        file. One call is one write in the storage contract's sense — the
        JSONL store passes exactly one complete line per call (ADR-0242). A
        short write is continued rather than dropped (#294), so one call can
        issue several ``os.write`` syscalls, and a second writer on the same
        file (#137) could land between them: nothing here promises byte-level
        atomicity. Not fsynced.
        """

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True, mode=SESSION_DIR_MODE)
        _tighten(p.parent, SESSION_DIR_MODE)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_BINARY
        fd = os.open(path, flags, SESSION_FILE_MODE)
        try:
            self._tighten_if_loose(fd)
            _write_all(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

    async def rename_file(self, source: str, destination: str) -> None:
        """Atomically replace ``destination`` with ``source`` (``os.replace``).

        Pi ``renameFile``. The publish half of a whole-file write: a reader
        opens either the old ``destination`` or the complete new file, never
        a prefix of it. Both paths must be on one filesystem, so callers
        stage the source beside its destination. On Windows a rename refused
        with ``PermissionError`` is retried for about a second, because a
        scanner or indexer briefly holding the just-closed file refuses it
        (:func:`_replace`).
        """

        await _replace(source, destination)

    @staticmethod
    def _tighten_if_loose(fd: int) -> None:
        """Drop group/other bits on an already-open session file.

        Sessions created before Track S2 exist at 0644; this is what
        migrates them, on the next write, without a separate upgrade
        step. Gated on ``fstat`` so the common (already-0600) case costs
        one cheap syscall and no ``chmod``.

        No-ops on Windows, deliberately. ``os.fchmod`` does not exist
        there, and the group/other bits this migrates do not either —
        access is an ACL question that has no mode-bit equivalent, so
        there is nothing for this function to tighten. Returning early
        is not merely defensive: the previous form raised
        ``AttributeError``, which ``suppress(OSError)`` does NOT catch
        (``AttributeError`` is no subclass of it), so every session
        write and append died on Windows. That single line was 200 of
        the 433 failures in the first windows CI run (#103 P0-b).
        """

        if sys.platform == "win32":
            return

        with contextlib.suppress(OSError):
            if os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, SESSION_FILE_MODE)

    async def list_dir(self, path: str) -> list[FileInfo]:
        p = Path(path)
        result: list[FileInfo] = []
        for child in p.iterdir():
            try:
                st = child.lstat()
            except OSError:
                continue
            if child.is_symlink():
                kind: FileKind = "symlink"
            elif child.is_dir():
                kind = "directory"
            else:
                kind = "file"
            result.append(
                FileInfo(
                    name=child.name,
                    path=str(child),
                    kind=kind,
                    size=st.st_size,
                    mtime_ms=st.st_mtime * 1000.0,
                )
            )
        return result

    async def exists(self, path: str) -> bool:
        return Path(path).exists()

    async def create_dir(self, path: str, *, recursive: bool = True) -> None:
        Path(path).mkdir(
            parents=recursive, exist_ok=recursive, mode=SESSION_DIR_MODE
        )
        _tighten(path, SESSION_DIR_MODE)

    async def remove(
        self, path: str, *, recursive: bool = False, force: bool = False
    ) -> None:
        p = Path(path)
        if not p.exists():
            if force:
                return
            raise FileNotFoundError(path)
        if p.is_dir():
            if recursive:
                shutil.rmtree(path)
            else:
                p.rmdir()
        else:
            p.unlink()

    async def copy_file(self, source: str, destination: str) -> None:
        """Sprint 6h₅b (ADR-0083, P-360) — Pi parity ``copyFile``.

        Used by :meth:`AgentSessionRuntime.import_from_jsonl` to clone a
        caller-supplied JSONL into the canonical sessions root when the
        source path differs from the destination. mtime is preserved so
        the imported file's metadata round-trips cleanly — ``find_most_recent``
        sorts on it.

        The copy is staged in a temp beside the destination and renamed over
        it only once it is complete (#294, ADR-0242). The previous form
        truncated the destination in place and then copied onto it, so a
        failure mid-copy left a half file — and when that name already held
        a complete session (a re-import), the session was gone. This is the
        store's only fsync: the rename can replace a complete file, and a
        rename that reaches the disk before the data it points at would
        leave an empty file after a power loss.

        The temp's name is unique per call, unlike a publish's
        ``<path>.tmp``. A publish targets a fresh session name, but an
        import's destination is named after its source, so two processes
        importing the same file share it: with one shared temp, one could
        remove or overwrite the other's half-written copy and then rename it
        into place. A crash between staging and the rename leaves
        ``<destination>.<hex>.tmp`` behind (owner-only, never listed, never
        swept).

        The temp is created at 0600 (``O_EXCL``, so never someone else's
        file) BEFORE any content lands in it. Copying first and tightening
        afterwards would leave a window where the imported prompts and tool
        results are group/world-readable. ``shutil.copyfile`` rather than
        ``copy2``: ``copy2`` ends with a ``copystat`` that re-applies the
        SOURCE's permission bits, which would undo the 0600 (measured — a
        0644 source produced a 0644 destination even when it was pre-created
        at 0600). ``copyfile`` is also byte-exact on every platform, which a
        copy through a text-mode descriptor is not.
        """

        dst = Path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True, mode=SESSION_DIR_MODE)
        _tighten(dst.parent, SESSION_DIR_MODE)
        src_stat = os.stat(source)
        temp = f"{destination}.{uuid.uuid4().hex[:12]}.tmp"
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY,
            SESSION_FILE_MODE,
        )
        try:
            os.close(fd)
            shutil.copyfile(source, temp)
            fd = os.open(temp, os.O_WRONLY | _O_BINARY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.utime(temp, (src_stat.st_atime, src_stat.st_mtime))
            await _replace(temp, destination)
        except BaseException:
            # Best effort: the original error is the one worth raising.
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise


__all__ = [
    "SESSION_DIR_MODE",
    "SESSION_FILE_MODE",
    "FileInfo",
    "FileKind",
    "FileSystem",
    "LocalFileSystem",
]
