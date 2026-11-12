"""A background agent's whole result, kept in a file beside its child session (#769, ADR-0295).

The owner's choice of 2026-11-12 (ADR-0295's note quotes the option): when a
background agent ends, its whole result — the text a foreground call would
have returned — is written to a 0600 file beside its child session, and once
the job service no longer keeps the job whole, ``job(action="result")``,
``/jobs result`` and ``agent(action="result")`` read it from there, as
``bash``'s output file is read.

WHERE. ``<children dir>/<run key>.result``: the directory a parent's child
session files live in (:func:`~aelix_agents.child_session.place_children`) and
the run's own spawn key, so a resume of the same child file gets a file of its
own. The path is derived, never read off a record (ADR-0295 §8's rule for the
child file): a reader derives it from the parent's session file and the key, and
checks what it opens — through ONE descriptor, not a link, one name, the size
and the sha256 the run recorded. A run with no child session file (a parent
outside a sessions bucket, or with no session file) writes none.

LIFETIME. It lives with the session, as the child session file does: nothing
in aelix removes it; deleting the session's children directory does.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass

from aelix_agents.child_session import SPAWN_KEY

RESULT_SUFFIX = ".result"
RESULT_RECORD_TYPE = "aelix.agent_result"
"""The client record that says a run's result file was written: ``{v, key, bytes, sha256}``."""

_WRITE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
)
# ``O_NONBLOCK``: a FIFO in its place opens at once and fails the regular-file
# check instead of blocking the event loop (verify-r2 F8).
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
)


@dataclass(frozen=True)
class ResultFile:
    """What a run recorded of the file it wrote: enough to find it again and to check it."""

    key: str
    size: int
    sha256: str

    def record(self) -> dict[str, object]:
        return {"v": 1, "key": self.key, "bytes": self.size, "sha256": self.sha256}

    @classmethod
    def from_record(cls, data: object) -> ResultFile | None:
        """The record's file, or ``None`` for a record of another shape."""

        if not isinstance(data, dict) or data.get("v") != 1:
            return None
        key, size, digest = data.get("key"), data.get("bytes"), data.get("sha256")
        if not isinstance(key, str) or not SPAWN_KEY.fullmatch(key):
            return None
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or not isinstance(digest, str):
            return None
        return cls(key=key, size=size, sha256=digest)


def result_path(directory: str, key: str) -> str | None:
    """``<directory>/<key>.result``, for a key of the spawn key's shape only."""

    if not SPAWN_KEY.fullmatch(key):
        return None
    return os.path.join(directory, key + RESULT_SUFFIX)


def write_result(directory: str, key: str, text: str) -> ResultFile | None:
    """Write ``text`` to ``<directory>/<key>.result``, 0600, through a temporary file and ``os.replace``.

    ``None`` when anything fails: the run then records no file, and the job
    service keeps the job whole (its reads could not be made from a file).
    """

    path = result_path(directory, key)
    if path is None:
        return None
    temporary = os.path.join(directory, f".{key}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        # A lone surrogate cannot be encoded: no file, and the job stays whole.
        data = text.encode("utf-8")
        fd = os.open(temporary, _WRITE_FLAGS, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError):
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        return None
    return ResultFile(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())


class Unreadable(str):
    """Why a result file could not be read, as ``result`` states it."""


def read_result(directory: str, file: ResultFile) -> str | Unreadable:
    """The text a run wrote, checked against what it recorded; or why it cannot be read."""

    path = result_path(directory, file.key)
    if path is None:
        return Unreadable("its result file's name is not one aelix writes")
    try:
        fd = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        return Unreadable("its result file no longer exists")
    except OSError:
        return Unreadable("its result file cannot be opened")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or info.st_size != file.size:
            return Unreadable("its result file is not the one aelix wrote")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
    except OSError:
        return Unreadable("its result file cannot be read")
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if hashlib.sha256(data).hexdigest() != file.sha256:
        return Unreadable("its result file is not the one aelix wrote")
    return data.decode("utf-8")


__all__ = [
    "RESULT_RECORD_TYPE",
    "RESULT_SUFFIX",
    "ResultFile",
    "Unreadable",
    "read_result",
    "result_path",
    "write_result",
]
