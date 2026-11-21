"""Checked, descriptor-relative storage for local POSIX lock attribution."""

from __future__ import annotations

import contextlib
import ctypes as C
import errno
import os
import stat
import struct
import sys
from collections.abc import Iterator
from pathlib import Path

_DIRECTORY = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC if os.name == "posix" else 0
_READ = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC if os.name == "posix" else 0
_LIBC = C.CDLL(None, use_errno=True)

# Linux filesystem magic numbers from linux/magic.h. Restrict to filesystems
# with local kernel file identities and no remote or userspace backing.
_LINUX_LOCAL = {0xEF53, 0x58465342, 0x9123683E, 0x01021994, 0xF2F52010}
_STORE = "aelix-session-locks-v2"


class _DarwinStatFS(C.Structure):
    _fields_ = [
        ("sizes", C.c_uint32 * 2),
        ("counts", C.c_uint64 * 5),
        ("fsid", C.c_int32 * 2),
        ("owner", C.c_uint32),
        ("type", C.c_uint32),
        ("flags", C.c_uint32),
        ("subtype", C.c_uint32),
        ("name", C.c_char * 16),
        ("mount", C.c_char * 1024),
        ("source", C.c_char * 1024),
        ("extended", C.c_uint32 * 8),
    ]


def _local(fd: int, *, ancestor: bool = False) -> bool:
    if sys.platform == "darwin":
        volume = _DarwinStatFS()
        return (
            _LIBC.fstatfs(fd, C.byref(volume)) == 0
            and bool(volume.flags & 0x1000)  # MNT_LOCAL
            and volume.name in (b"apfs", b"hfs")
        )
    if sys.platform == "linux":
        # f_type is the first native long in Linux's statfs layout.
        volume = (C.c_long * 64)()
        if _LIBC.fstatfs(fd, C.byref(volume)) != 0:
            return False
        magic = volume[0] & 0xFFFFFFFF
        # Container images can have an overlay root with a checked local
        # tmpfs/data mount below it. The proof objects must be on the latter.
        return magic in _LINUX_LOCAL or (ancestor and magic == 0x794C7630)
    return False


def _darwin_acl_safe(fd: int) -> bool:
    # The standard macOS home/Library/Caches ACL contains an everyone-deny-delete
    # ACE. Denies cannot expand mode permissions. Reject all extended grants.
    get_acl = _LIBC.acl_get_fd_np
    get_acl.restype = C.c_void_p
    acl = get_acl(fd, 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        return C.get_errno() == errno.ENOENT
    try:
        entry = C.c_void_p()
        tag = C.c_int()
        index = 0  # ACL_FIRST_ENTRY
        while True:
            result = _LIBC.acl_get_entry(C.c_void_p(acl), index, C.byref(entry))
            if result != 0:
                # Darwin returns -1 with EINVAL at the end of the ACL.
                return C.get_errno() == errno.EINVAL
            if _LIBC.acl_get_tag_type(entry, C.byref(tag)) != 0 or tag.value != 2:
                return False
            index = -1  # ACL_NEXT_ENTRY
    finally:
        _LIBC.acl_free(C.c_void_p(acl))


def _acl_safe(fd: int) -> bool:
    if sys.platform == "darwin":
        return _darwin_acl_safe(fd)
    if sys.platform == "linux":
        for name in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                os.getxattr(fd, name)
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    return False
            else:
                return False
        return True
    return False


def _uid() -> int:
    if sys.platform == "win32":
        raise OSError("POSIX lock proof unavailable")
    return os.geteuid()


def _check_dir(fd: int) -> None:
    info = os.fstat(fd)
    writable = info.st_mode & 0o022
    # In a sticky parent, the already opened child is checked separately for
    # trusted ownership. Other users cannot replace it (e.g. /private/tmp).
    if (
        info.st_uid not in (0, _uid())
        or (writable and not info.st_mode & stat.S_ISVTX)
        or not _local(fd, ancestor=True)
        or not _acl_safe(fd)
    ):
        raise OSError("untrusted lock proof directory")


@contextlib.contextmanager
def _directories(path: Path, *, create_tail: int = 0) -> Iterator[int]:
    if path.anchor != "/" or ".." in path.parts:
        raise OSError("noncanonical lock proof path")
    with contextlib.ExitStack() as stack:
        fd = os.open("/", _DIRECTORY)
        stack.callback(os.close, fd)
        _check_dir(fd)
        for index, part in enumerate(path.parts[1:], 1):
            if index > len(path.parts) - 1 - create_tail:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
            fd = os.open(part, _DIRECTORY, dir_fd=fd)
            stack.callback(os.close, fd)
            _check_dir(fd)
        yield fd


def _cache_base() -> Path:
    # Use the OS account database, independent of HOME and the agent directory.
    if sys.platform == "win32":
        raise OSError("POSIX account cache unavailable")
    import pwd  # noqa: PLC0415

    home = Path(pwd.getpwuid(_uid()).pw_dir)
    return home / ("Library/Caches" if sys.platform == "darwin" else ".cache")


@contextlib.contextmanager
def locator_directory(*, create: bool = False) -> Iterator[int]:
    with _directories(_cache_base() / _STORE, create_tail=2 if create else 0) as fd:
        info = os.fstat(fd)
        if info.st_uid != _uid() or stat.S_IMODE(info.st_mode) != 0o700 or not _local(fd):
            raise OSError("lock proof directory is not private")
        yield fd


def held_identity(fd: int) -> bytes:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid not in (0, _uid())
        or info.st_mode & 0o022
        or not info.st_ino
        or not _local(fd)
        or not _acl_safe(fd)
    ):
        raise OSError("unusable lock proof identity")
    return struct.pack(">BQQ8x", 1, info.st_dev, info.st_ino)


@contextlib.contextmanager
def named_identity(path: str) -> Iterator[bytes]:
    slot = Path(path)
    with _directories(slot.parent) as parent:
        fd = os.open(slot.name, _READ, dir_fd=parent)
        try:
            yield held_identity(fd)
        finally:
            os.close(fd)


@contextlib.contextmanager
def read_locator(key: str) -> Iterator[tuple[bytes, bytes]]:
    with locator_directory() as parent:
        fd = os.open(key, _READ, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if info.st_uid != _uid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise OSError("lock proof locator is not private")
            yield held_identity(fd), os.read(fd, 161)
        finally:
            os.close(fd)


def publish_locator(key: str, temp: str, record: bytes) -> None:
    with locator_directory(create=True) as parent:
        fd = os.open(temp, _READ | os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if info.st_uid != _uid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise OSError("lock proof temporary file is not private")
            held_identity(fd)
            if os.write(fd, record) != len(record):
                raise OSError("incomplete lock proof locator")
            os.replace(temp, key, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp, dir_fd=parent)
