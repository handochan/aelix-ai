"""``FileSystem`` Protocol + ``LocalFileSystem`` impl (Sprint 4a).

Pi source: ``packages/agent/src/harness/types.ts:273-323`` (``FileSystem``)
+ ``packages/agent/src/harness/env/nodejs.ts``. Aelix simplifies by raising
``OSError`` directly instead of Pi's ``Result<T, FileError>`` ADT (idiomatic
Python). The JSONL boundary wraps these into
:class:`SessionError("storage", ...)` so the public surface stays Pi-shaped.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

FileKind = Literal["file", "directory", "symlink"]


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


class LocalFileSystem:
    """Default ``FileSystem`` wrapping ``pathlib`` / ``os``.

    All async methods are thin wrappers; blocking I/O is moved off the event
    loop via ``asyncio.to_thread`` only at the JSONL boundary where
    appropriate (Sprint 4a accepts in-thread blocking for ``list_dir`` /
    ``exists`` because those run rarely).
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
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def append_file(self, path: str, content: str) -> None:
        """Append using POSIX ``O_APPEND`` semantics for ≤ PIPE_BUF atomicity."""

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # POSIX O_APPEND: writes ≤ PIPE_BUF (4096B) are atomic.
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)

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
        Path(path).mkdir(parents=recursive, exist_ok=recursive)

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


__all__ = ["FileInfo", "FileKind", "FileSystem", "LocalFileSystem"]
