"""Turn ``mcp.json`` servers on and off, changing only their ``enabled`` bytes (ADR-0305 D14).

Two halves:

- :func:`edit_enabled` is pure. It splices the ``enabled`` literals of the named
  servers (``true`` <-> ``false``, or an inserted ``"enabled": false`` when the
  key is absent) and returns the new bytes, or the old bytes when nothing
  changes. Every other byte is kept: other keys and servers, their order,
  whitespace, escapes, number spellings, line endings and the trailing newline.
  It refuses, raising :class:`McpRefused`, unless four checks pass:

  1. strict input: UTF-8 with no BOM, JSON with no duplicate key anywhere and no
     ``NaN``/``Infinity``; every ``ValueError`` and ``RecursionError`` from the
     parse or the span scanner is a refusal, never an exception;
  2. shape: ``mcpServers`` is an object, each named server is in it as a
     non-empty object, and an existing ``enabled`` is the literal ``true`` or
     ``false``;
  3. the byte guard: the new text is the old text with exactly the planned
     splices at the planned offsets, each an allowed kind, and nothing else;
  4. the value guard: the new text parses with the same strict hooks, and its
     value is the old value with only the asked ``enabled`` members set,
     compared type by type (``True`` is not ``1``, ``1.0`` is not ``1``) and in
     key order.

  So a splice bug can refuse; it cannot return a wrong file.

- :func:`write_enabled` is the locked I/O around it, run by its caller in a
  worker thread (``asyncio.to_thread``). It locks the REAL target's directory
  (POSIX ``flock`` on a read-only descriptor of it; Windows a named mutex),
  polled to a deadline while honouring a cancel; then calls the caller's
  ``authorize`` (the file in force and its trust, re-resolved inside the lock,
  ADR-0305 D13) and checks the target still resolves to the path it locked;
  refuses a file it cannot replace without changing who may read it; reads
  BYTES, edits, writes a temp file beside it carrying owner, group, mode bits and
  (Linux) every xattr including the POSIX ACL, (macOS) the ACL and xattrs; on
  Windows compares the temp file's owner, group and DACL with the original's;
  checks the cancel; and before each replace attempt checks that the file is
  still the bytes it read with the identity it read (device, inode, size, mtime,
  mode, owner, group and ctime, so a chmod, chown, ACL or xattr change made before
  the last check is refused, not undone (ADR-0305 Limits: the window after it), and replaces it.

Every refusal text names a file, a server or a key, never a value: the file holds
credentials (``env``, ``headers``, a url's query), and these texts reach the TUI,
RPC clients and bug reports. This module imports no ``rich`` and no MCP SDK.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import functools
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

LOCK_TIMEOUT_S: float = 30.0
"""How long a save waits for another save of the same file (ADR-0279 D4's 30 s)."""

_POLL_S = 0.05
_REPLACE_RETRY_S = 2.0
_RETRY_REPLACE = sys.platform == "win32"
"""Retry a replace a reader's sharing violation refused (Windows); a seam for tests."""
_COMPARE_DESCRIPTORS = sys.platform == "win32"
"""Compare the temp file's owner, group and DACL with the original's (Windows); a seam."""
_MAX_CHANGES = 1024
_BY_HAND = "edit it by hand"
_SERVER_BY_HAND = "edit the file by hand"

_WS = " \t\r\n"
_WS_RE = re.compile(r"[ \t\r\n]*")
_STRING_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"', re.S)
_NUM_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_CLOSE_RE = re.compile(r"[ \t\r\n]*\}")
_INSERT_RE = re.compile(r',(?:\r?\n[ \t]*|[ \t]*)"enabled"[ \t]*:[ \t]*false\Z')
_SEP_RE = re.compile(r"[ \t]*:[ \t]*\Z")
_GAP_RE = re.compile(r",[ \t]*\Z")
_FLIPS = (("true", "false"), ("false", "true"))
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class McpRefused(Exception):
    """Nothing was written. The text names a file, a server or a key, never a value."""


@dataclass(frozen=True)
class McpWriteOutcome:
    """What :func:`write_enabled` did."""

    status: Literal["written", "unchanged", "refused", "abandoned"]
    detail: str = ""
    """The refusal text for ``"refused"``; never a value from the file."""


class _Abandoned(Exception):
    """The caller set ``abandoned``; nothing was written."""


class _ScanError(Exception):
    pass


# --- the strict parse (check 1) -----------------------------------------------------


def _name(key: str) -> str:
    """A key or a server name for a message: printable and bounded."""

    text = "".join(char if char.isprintable() else "?" for char in key)
    return text if len(text) <= 64 else text[:63] + "…"


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise McpRefused(f'two members named "{_name(key)}" in one object; {_BY_HAND}')
        out[key] = value
    return out


def _no_constant(_constant: str) -> object:
    raise McpRefused(f"the file holds NaN or Infinity, which JSON does not allow; {_BY_HAND}")


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise McpRefused(
            f"the file starts with a byte-order mark, which aelix does not read; {_BY_HAND}"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise McpRefused(f"the file is not UTF-8, which aelix does not read; {_BY_HAND}") from None


def _parse(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except McpRefused:
        raise
    except (ValueError, RecursionError):
        raise McpRefused(f"the file is not valid JSON; {_BY_HAND}") from None


# --- the span scanner (run only on text _parse accepted) -----------------------------


@dataclass
class _Node:
    kind: str  # object, array, string, number, literal
    start: int
    end: int
    members: list[_Member] = field(default_factory=list)


@dataclass
class _Member:
    key: str
    key_start: int
    key_end: int
    value: _Node


@dataclass(frozen=True)
class _Splice:
    offset: int
    removed: str
    inserted: str


def _skip(s: str, i: int) -> int:
    match = _WS_RE.match(s, i)
    return match.end() if match else i


def _expect(s: str, i: int, char: str) -> None:
    if i >= len(s) or s[i] != char:
        raise _ScanError(i)


def _string_end(s: str, i: int) -> int:
    match = _STRING_RE.match(s, i)
    if match is None:
        raise _ScanError(i)
    return match.end()


def _object(s: str, i: int) -> _Node:
    node = _Node("object", i, -1)
    j = _skip(s, i + 1)
    if j < len(s) and s[j] == "}":
        node.end = j + 1
        return node
    while True:
        j = _skip(s, j)
        key_end = _string_end(s, j)
        key = json.loads(s[j:key_end])
        colon = _skip(s, key_end)
        _expect(s, colon, ":")
        value = _value(s, colon + 1)
        node.members.append(_Member(key, j, key_end, value))
        j = _skip(s, value.end)
        if j < len(s) and s[j] == ",":
            j += 1
            continue
        _expect(s, j, "}")
        node.end = j + 1
        return node


def _array(s: str, i: int) -> _Node:
    j = _skip(s, i + 1)
    if j < len(s) and s[j] == "]":
        return _Node("array", i, j + 1)
    while True:
        value = _value(s, j)
        j = _skip(s, value.end)
        if j < len(s) and s[j] == ",":
            j += 1
            continue
        _expect(s, j, "]")
        return _Node("array", i, j + 1)


def _value(s: str, i: int) -> _Node:
    i = _skip(s, i)
    if i >= len(s):
        raise _ScanError(i)
    char = s[i]
    if char == "{":
        return _object(s, i)
    if char == "[":
        return _array(s, i)
    if char == '"':
        return _Node("string", i, _string_end(s, i))
    for literal in ("true", "false", "null"):
        if s.startswith(literal, i):
            return _Node("literal", i, i + len(literal))
    match = _NUM_RE.match(s, i)
    if match is None:
        raise _ScanError(i)
    return _Node("number", i, match.end())


def _scan(text: str) -> _Node:
    try:
        root = _value(text, 0)
        if _skip(text, root.end) != len(text):
            raise _ScanError(root.end)
        return root
    except (_ScanError, ValueError, RecursionError, IndexError):
        raise McpRefused(f"the file could not be read for editing; {_BY_HAND}") from None


def _member(obj: _Node, key: str) -> _Member | None:
    return next((member for member in obj.members if member.key == key), None)


# --- the plan: one splice per server, at most (check 2) ------------------------------


def _insertion(text: str, obj: _Node) -> _Splice:
    """``"enabled": false`` after the object's last member, in the object's own style.

    The separator is the last member's when it is ``[ \\t]*:[ \\t]*``. When the last
    member starts its line (only spaces or tabs before its key, on a line inside
    the object), the new member gets its own line with that indent and the
    newline that ends the line before (CRLF stays CRLF). Otherwise it goes inline,
    after the gap between the last two members when that is ``,[ \\t]*``, else
    after ``", "`` (``","`` for a single member right after the brace).
    """

    last = obj.members[-1]
    sep = text[last.key_end : last.value.start]
    if not _SEP_RE.match(sep):
        sep = ": "
    line_start = text.rfind("\n", 0, last.key_start) + 1
    indent = text[line_start : last.key_start]
    if indent.strip(" \t") == "":  # it starts its line (the object's "{" would show here)
        newline = "\r\n" if text[line_start - 2 : line_start] == "\r\n" else "\n"
        lead = "," + newline + indent
    elif len(obj.members) > 1:
        gap = text[obj.members[-2].value.end : last.key_start]
        lead = gap if _GAP_RE.match(gap) else ", "
    else:
        lead = ", " if text[obj.start + 1 : obj.start + 2] in (" ", "\t") else ","
    return _Splice(last.value.end, "", lead + '"enabled"' + sep + "false")


def _server_splice(text: str, servers: _Node, name: str, want: bool) -> _Splice | None:
    entry = _member(servers, name)
    if entry is None:
        raise McpRefused(f'server "{_name(name)}" is not in "mcpServers" now; {_SERVER_BY_HAND}')
    obj = entry.value
    if obj.kind != "object" or not obj.members:
        raise McpRefused(f'server "{_name(name)}" is not an object with members; {_SERVER_BY_HAND}')
    enabled = _member(obj, "enabled")
    literal = "true" if want else "false"
    if enabled is None:
        return None if want else _insertion(text, obj)
    current = text[enabled.value.start : enabled.value.end]
    if current not in ("true", "false"):
        raise McpRefused(
            f'server "{_name(name)}": "enabled" is not true or false; {_SERVER_BY_HAND}'
        )
    return None if current == literal else _Splice(enabled.value.start, current, literal)


def _plan(text: str, root: _Node, changes: Sequence[tuple[str, bool]]) -> list[_Splice]:
    if root.kind != "object":
        raise McpRefused(f'the file is not a JSON object with "mcpServers"; {_BY_HAND}')
    servers = _member(root, "mcpServers")
    if servers is None or servers.value.kind != "object":
        raise McpRefused(f'the file has no "mcpServers" object; {_BY_HAND}')
    splices = [_server_splice(text, servers.value, name, want) for name, want in changes]
    return sorted((sp for sp in splices if sp is not None), key=lambda sp: sp.offset)


def _apply(text: str, splices: Sequence[_Splice]) -> str:
    out: list[str] = []
    at = 0
    for sp in splices:
        out.append(text[at : sp.offset])
        out.append(sp.inserted)
        at = sp.offset + len(sp.removed)
    out.append(text[at:])
    return "".join(out)


# --- the guards (checks 3 and 4) ------------------------------------------------------

_GUARD_BYTES = f'the edit would change bytes other than "enabled"; nothing written; {_BY_HAND}'
_GUARD_PLACE = f"the edit was not at its planned place; nothing written; {_BY_HAND}"
_GUARD_VALUE = (
    f'the edited file would hold more than the asked "enabled"; nothing written; {_BY_HAND}'
)


def _check_splice(old: str, sp: _Splice, i: int, last_end: int) -> None:
    """One splice is an allowed kind, in order, and (an insertion) at an allowed place."""

    allowed = (sp.removed, sp.inserted) in _FLIPS or (
        sp.removed == "" and _INSERT_RE.match(sp.inserted) is not None
    )
    if not allowed or sp.offset < i or sp.offset <= last_end:
        raise McpRefused(_GUARD_BYTES)
    if sp.removed == "" and (
        sp.offset == 0 or old[sp.offset - 1] in _WS or _CLOSE_RE.match(old, sp.offset) is None
    ):
        # Right after a value, with only whitespace before the closing brace: after
        # an object's LAST member. Which object, the value guard checks.
        raise McpRefused(_GUARD_PLACE)
    if old[sp.offset : sp.offset + len(sp.removed)] != sp.removed:
        raise McpRefused(_GUARD_PLACE)


def _check_bytes(old: str, new: str, splices: Sequence[_Splice]) -> None:
    """``new`` is ``old`` with exactly *splices*, and every other byte identical."""

    i = j = 0
    last_end = -1
    for sp in splices:
        _check_splice(old, sp, i, last_end)
        keep = sp.offset - i
        if new[j : j + keep] != old[i : sp.offset]:
            raise McpRefused(_GUARD_BYTES)
        j += keep
        if new[j : j + len(sp.inserted)] != sp.inserted:
            raise McpRefused(_GUARD_BYTES)
        j += len(sp.inserted)
        i = last_end = sp.offset + len(sp.removed)
    if new[j:] != old[i:]:
        raise McpRefused(_GUARD_BYTES)


def _same(a: object, b: object) -> bool:
    """Equal type by type: ``True`` is not ``1``, ``1.0`` is not ``1``; key order counts."""

    if type(a) is not type(b):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _check_value(old_value: object, new: str, changes: Sequence[tuple[str, bool]]) -> None:
    expected = cast("dict[str, Any]", copy.deepcopy(old_value))  # _plan refused any other shape
    for name, want in changes:
        entry = expected["mcpServers"][name]
        if "enabled" in entry or not want:
            # Off with no key: the member is appended, so it comes last here too.
            # On with no key: absent already means on, and stays absent.
            entry["enabled"] = want
    if not _same(_parse(new), expected):
        raise McpRefused(_GUARD_VALUE)


# --- the pure entry point ---------------------------------------------------------------


def _check_changes(changes: Sequence[tuple[str, bool]]) -> None:
    if len(changes) > _MAX_CHANGES:
        raise McpRefused("too many servers in one save")
    seen: set[str] = set()
    for name, want in changes:
        if type(name) is not str or type(want) is not bool:
            raise McpRefused("a change names a server and says true or false")
        if name in seen:
            raise McpRefused(f'server "{_name(name)}" is named twice in one save')
        seen.add(name)


def _edit(data: bytes, changes: Sequence[tuple[str, bool]]) -> bytes:
    text = _decode(data)
    old_value = _parse(text)
    root = _scan(text)
    splices = _plan(text, root, changes)
    if not splices:
        return data
    new = _apply(text, splices)
    _check_bytes(text, new, splices)
    _check_value(old_value, new, changes)
    return new.encode("utf-8")


def edit_enabled(data: bytes, changes: Sequence[tuple[str, bool]]) -> bytes:
    """*data* with each named server's ``enabled`` set as asked, or *data* itself.

    ``(name, True)`` turns a ``false`` into ``true`` and leaves an absent key absent
    (absent means on); ``(name, False)`` turns a ``true`` into ``false`` or inserts
    ``"enabled": false`` after the server's last member. Raises
    :class:`McpRefused`, and only that, when any of the four checks fails.
    """

    _check_changes(changes)
    try:
        return _edit(data, changes)
    except RecursionError:
        raise McpRefused(f"the file is nested too deeply to edit safely; {_BY_HAND}") from None


# --- the lock -------------------------------------------------------------------------------


def _mutex_name(real: str) -> str:
    """Windows: one named mutex per real file, shared by every process in the logon session."""

    digest = hashlib.sha256(os.path.normcase(real).encode("utf-8", "surrogatepass")).hexdigest()
    return "Local\\aelix-mcp-" + digest[:32]


def _poll(
    try_once: Callable[[], bool], deadline_s: float, abandoned: threading.Event, label: str
) -> None:
    end = time.monotonic() + deadline_s
    while not try_once():
        if abandoned.wait(_POLL_S):
            raise _Abandoned
        if time.monotonic() >= end:
            raise McpRefused(
                f"another aelix has been saving {label} for {deadline_s:.0f} s; "
                "nothing written; try again"
            )


def _after_read() -> None:
    """Test seam: runs between the read and the edit, inside the lock."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _WAIT_OBJECT_0 = 0x0
    _WAIT_ABANDONED = 0x80
    _WAIT_TIMEOUT = 0x102
    _DACL_SECURITY_INFORMATION = 0x4

    @functools.cache
    def _kernel32() -> ctypes.WinDLL:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        lib.CreateMutexW.restype = wintypes.HANDLE
        lib.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        lib.WaitForSingleObject.restype = wintypes.DWORD
        lib.ReleaseMutex.argtypes = [wintypes.HANDLE]
        lib.ReleaseMutex.restype = wintypes.BOOL
        lib.CloseHandle.argtypes = [wintypes.HANDLE]
        lib.CloseHandle.restype = wintypes.BOOL
        return lib

    @functools.cache
    def _advapi32() -> ctypes.WinDLL:
        return ctypes.WinDLL("advapi32", use_last_error=True)

    def _try_mutex(handle: int) -> bool:
        result = _kernel32().WaitForSingleObject(handle, 0)
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            return True
        if result == _WAIT_TIMEOUT:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    @contextlib.contextmanager
    def _platform_lock(
        real: str, deadline_s: float, abandoned: threading.Event, label: str
    ) -> Iterator[None]:
        """A named mutex per real file, polled to the deadline, released in this thread."""

        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, _mutex_name(real))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _poll(functools.partial(_try_mutex, handle), deadline_s, abandoned, label)
            try:
                yield
            finally:
                kernel32.ReleaseMutex(handle)
        finally:
            kernel32.CloseHandle(handle)

    def _access_refusal(real: str, label: str) -> str | None:
        """Windows: nothing is refused here; :func:`_check_access_kept` decides.

        The replacement's owner, group and DACL must equal the original's, compared on
        the still-empty temp and again before each replace attempt. A temp gets what any
        new file in that folder gets, so an original with a protected DACL (a new file's
        never is), or with ACEs other than a new file's there, is refused. An earlier
        "inherited ACEs only" pre-check refused every ordinary file in the runner's
        ``tmp_path`` on windows-latest (CI run 44219585507, 50 failures on each Python).
        """

        del real, label
        return None

    _OWNER_GROUP_DACL = 0x1 | 0x2 | _DACL_SECURITY_INFORMATION
    _SE_FILE_OBJECT = 1
    _SDDL_REVISION_1 = 1

    @functools.cache
    def _security_api() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
        api = _advapi32()
        api.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        api.GetNamedSecurityInfoW.restype = wintypes.DWORD
        api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        api.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
        kernel32 = _kernel32()
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return api, kernel32

    def _descriptor(path: str) -> str | None:
        """*path*'s owner, group and DACL as an SDDL string, or ``None`` if unreadable."""

        api, kernel32 = _security_api()
        descriptor = ctypes.c_void_p()
        if (
            api.GetNamedSecurityInfoW(
                path,
                _SE_FILE_OBJECT,
                _OWNER_GROUP_DACL,
                None,
                None,
                None,
                None,
                ctypes.byref(descriptor),
            )
            != 0
            or not descriptor.value
        ):
            return None
        try:
            text = ctypes.c_void_p()
            if (
                not api.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor, _SDDL_REVISION_1, _OWNER_GROUP_DACL, ctypes.byref(text), None
                )
                or not text.value
            ):
                return None
            try:
                return ctypes.wstring_at(text.value)
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.LocalFree(descriptor)

    def _carry_metadata(src_fd: int, tmp_fd: int, st: os.stat_result, label: str) -> None:
        """Windows: the temp inherits the directory's ACEs and gets this process's owner;
        :func:`_check_access_kept` compares them with the original's before the replace."""

    def _clear_acl(fd: int) -> None:
        """Windows: nothing was carried onto the temp file."""

else:
    import fcntl

    def _try_flock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    @contextlib.contextmanager
    def _platform_lock(
        real: str, deadline_s: float, abandoned: threading.Event, label: str
    ) -> Iterator[None]:
        """``flock`` on a read-only descriptor of the real file's directory, polled.

        One lock domain per directory, whatever agent dir a process runs with,
        and nothing is created in the folder.
        """

        fd = os.open(os.path.dirname(real), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            _poll(functools.partial(_try_flock, fd), deadline_s, abandoned, label)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _access_refusal(real: str, label: str) -> str | None:
        """POSIX: owner, group, mode and the ACL are carried, not refused up front."""

        return None

    def _descriptor(path: str) -> str | None:
        """POSIX: no Windows security descriptor (``_COMPARE_DESCRIPTORS`` is off)."""

        del path
        return None

    def _carry_owner(tmp_fd: int, st: os.stat_result, label: str) -> None:
        now = os.fstat(tmp_fd)
        if (now.st_uid, now.st_gid) == (st.st_uid, st.st_gid):
            return
        try:
            os.fchown(tmp_fd, st.st_uid, st.st_gid)
        except OSError:
            raise McpRefused(
                f"{label}'s owner or group cannot be kept on a replacement; {_BY_HAND}"
            ) from None

    def _carry_metadata(src_fd: int, tmp_fd: int, st: os.stat_result, label: str) -> None:
        """Owner, group, the exact mode bits and the ACL/xattrs, or a refusal."""

        _carry_owner(tmp_fd, st, label)
        _carry_extended(src_fd, tmp_fd, label)
        os.fchmod(tmp_fd, stat.S_IMODE(st.st_mode))
        now = os.fstat(tmp_fd)
        if (now.st_uid, now.st_gid, stat.S_IMODE(now.st_mode)) != (
            st.st_uid,
            st.st_gid,
            stat.S_IMODE(st.st_mode),
        ):
            raise McpRefused(
                f"{label}'s owner, group or mode bits cannot be kept on a replacement; {_BY_HAND}"
            )

    def _extended_refusal(label: str) -> McpRefused:
        return McpRefused(
            f"{label}'s ACL or extended attributes cannot be kept on a replacement; {_BY_HAND}"
        )

    if sys.platform == "darwin":
        import ctypes
        import ctypes.util

        _COPYFILE_ACL = 1 << 0
        _COPYFILE_XATTR = 1 << 2
        _ACL_TYPE_EXTENDED = 0x00000100

        @functools.cache
        def _darwin_libc() -> ctypes.CDLL | None:
            try:
                libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            except OSError:
                return None
            libc.fcopyfile.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            libc.fcopyfile.restype = ctypes.c_int
            libc.acl_init.argtypes = [ctypes.c_int]
            libc.acl_init.restype = ctypes.c_void_p
            libc.acl_set_fd_np.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            libc.acl_set_fd_np.restype = ctypes.c_int
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free.restype = ctypes.c_int
            return libc

        def _carry_extended(src_fd: int, tmp_fd: int, label: str) -> None:
            """macOS: the ACL and the xattrs, by ``fcopyfile``."""

            libc = _darwin_libc()
            if libc is None or libc.fcopyfile(
                src_fd, tmp_fd, None, _COPYFILE_ACL | _COPYFILE_XATTR
            ):
                raise _extended_refusal(label)

        def _clear_acl(fd: int) -> None:
            """macOS: drop the ACL carried onto a temp file, so it can be removed (deny delete)."""

            libc = _darwin_libc()
            if libc is None:
                return
            empty = libc.acl_init(0)
            if not empty:
                return
            try:
                libc.acl_set_fd_np(fd, empty, _ACL_TYPE_EXTENDED)
            finally:
                libc.acl_free(empty)

    elif sys.platform == "linux":

        def _xattr_names(fd: int) -> list[str]:
            try:
                return os.listxattr(fd)
            except OSError as exc:
                if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                    return []  # a filesystem without xattrs has none to carry
                raise

        def _carry_extended(src_fd: int, tmp_fd: int, label: str) -> None:
            """Linux: every xattr, ``system.posix_acl_access`` included, and none more."""

            try:
                names = _xattr_names(src_fd)
                present = _xattr_names(tmp_fd)
                for name in names:
                    value = os.getxattr(src_fd, name)
                    if name not in present or os.getxattr(tmp_fd, name) != value:
                        os.setxattr(tmp_fd, name, value)
                for name in present:
                    if name not in names:
                        # e.g. an ACL the temp inherited from a default ACL the
                        # original never had.
                        os.removexattr(tmp_fd, name)
            except OSError:
                raise _extended_refusal(label) from None

        def _clear_acl(fd: int) -> None:
            """Linux: an ACL cannot deny the temp file's removal."""

    else:

        def _carry_extended(src_fd: int, tmp_fd: int, label: str) -> None:
            """Other POSIX systems: owner, group and mode bits only."""

        def _clear_acl(fd: int) -> None:
            """Other POSIX systems: nothing was carried."""


# --- the write --------------------------------------------------------------------------------


def _identity(st: os.stat_result) -> tuple[int, ...]:
    """The file as read: which file, its size and mtime, and who may read or write it.

    ``st_mode``, owner and group are what a replacement carries; ``st_ctime_ns``
    moves on any metadata change (a chmod, a chown, an ACL or an xattr) on POSIX,
    so one made after the read but before the last check is refused, not undone. (Windows'
    ``st_ctime`` is the creation time; there :func:`_check_access_kept` compares
    owner, group and DACL just before each replace attempt.)
    """

    return (
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_ctime_ns,
    )


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _refuse_unsafe(real: str, label: str, st: os.stat_result) -> None:
    """What a replacement cannot keep, refused before anything is written."""

    if not stat.S_ISREG(st.st_mode):
        raise McpRefused(f"{label} is not a regular file; {_BY_HAND}")
    if st.st_nlink > 1:
        raise McpRefused(
            f"{label} has another hard link, which a replacement would split; {_BY_HAND}"
        )
    read_only = getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY
    # The owner's write bit is the user's own "read-only", root included.
    if read_only or not st.st_mode & stat.S_IWUSR or not os.access(real, os.W_OK):
        raise McpRefused(f"{label} is read-only; {_BY_HAND}")
    refusal = _access_refusal(real, label)
    if refusal is not None:
        raise McpRefused(refusal)


def _read_checked(real: str, label: str) -> tuple[os.stat_result, bytes]:
    fd = os.open(real, _READ_FLAGS)
    try:
        st = os.fstat(fd)
        _refuse_unsafe(real, label, st)
        return st, _read_all(fd)
    finally:
        os.close(fd)


def _carry_from(real: str, tmp_fd: int, st: os.stat_result, label: str) -> None:
    """Carry the file's metadata onto the temp file (another file there is refused later,
    by :func:`_check_unchanged`, before anything is replaced)."""

    src_fd = os.open(real, _READ_FLAGS)
    try:
        _carry_metadata(src_fd, tmp_fd, st, label)
    finally:
        os.close(src_fd)


def _changed(label: str) -> str:
    return f"{label} changed while it was being saved; nothing written; try again"


def _check_unchanged(real: str, label: str, st: os.stat_result, data: bytes) -> None:
    """Still the file that was read, and still its bytes (a coarse mtime can hide a write)."""

    fd = os.open(real, _READ_FLAGS)
    try:
        same = _identity(os.fstat(fd)) == _identity(st) and _read_all(fd) == data
    finally:
        os.close(fd)
    if not same:
        raise McpRefused(_changed(label))


def _check_access_kept(real: str, tmp: str, label: str) -> None:
    """Windows: the replacement's owner, group and DACL are the original's, or a refusal.

    A temp file gets the creating process's owner and the directory's inheritable
    ACEs; an original whose inherited ACEs no longer match its parent's (a restore,
    an API that changed them), or whose owner is someone else, would change who may
    read it. The two descriptors are compared as SDDL strings.
    """

    original, replacement = _descriptor(real), _descriptor(tmp)
    if original is None or replacement is None:
        raise McpRefused(f"{label}'s access rules cannot be read; {_BY_HAND}")
    if original != replacement:
        raise McpRefused(f"{label} has its own access rules; {_BY_HAND}")


def _check_before_replace(tmp: str, real: str, label: str, st: os.stat_result, data: bytes) -> None:
    """Before each replace attempt: the file read, with its bytes, and (Windows) its access."""

    _check_unchanged(real, label, st, data)
    if _COMPARE_DESCRIPTORS:
        _check_access_kept(real, tmp, label)


def _replace(tmp: str, real: str, label: str, st: os.stat_result, data: bytes) -> None:
    """``os.replace``, retried on Windows while a reader holds *real* open.

    As ``aelix_ai/settings/storage.py`` ``_replace``: 5 ms doubling to 100 ms, for
    at most ``_REPLACE_RETRY_S``. Elsewhere it is one call. Every attempt, the
    first included, is preceded by :func:`_check_before_replace`, so an edit that
    lands while a retry waits is refused, not overwritten. What remains is the
    moment between that check and the rename, against a writer that does not
    take this lock (an editor).
    """

    deadline = time.monotonic() + _REPLACE_RETRY_S
    delay = 0.005
    while True:
        _check_before_replace(tmp, real, label, st, data)
        try:
            os.replace(tmp, real)
            return
        except PermissionError:
            if not _RETRY_REPLACE or time.monotonic() + delay > deadline:
                raise McpRefused(
                    f"{label} cannot be replaced: its access rules or another program "
                    f"prevent it; {_BY_HAND}"
                ) from None
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def _discard(fd: int | None, tmp: str) -> None:
    """Remove the temp, closing *fd* first: Windows cannot delete a file this process
    holds open (``tempfile.mkstemp`` does not open it for delete sharing)."""

    if fd is not None:
        try:
            # The temp carries the original's ACL; one that denies delete would keep it.
            _clear_acl(fd)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
    with contextlib.suppress(OSError):
        os.unlink(tmp)


def _replace_file(
    real: str,
    label: str,
    st: os.stat_result,
    data: bytes,
    new: bytes,
    abandoned: threading.Event,
) -> None:
    directory, base = os.path.split(real)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{base}.", suffix=".tmp")
    open_fd: int | None = fd
    try:
        if _COMPARE_DESCRIPTORS:
            # While the temp is still empty: a file whose access already differs from
            # what a replacement would get never has its new content written beside it.
            _check_access_kept(real, tmp, label)
        _write_all(fd, new)
        _carry_from(real, fd, st, label)
        os.fsync(fd)
        if sys.platform == "win32":
            open_fd = None  # closed once, here, whatever the close does
            os.close(fd)  # Windows cannot rename a file this process holds open
        if abandoned.is_set():
            raise _Abandoned
        _replace(tmp, real, label, st, data)
    except BaseException:
        discard_fd, open_fd = open_fd, None
        _discard(discard_fd, tmp)
        raise
    finally:
        if open_fd is not None:
            os.close(open_fd)


def _write_locked(
    real: str, label: str, changes: Sequence[tuple[str, bool]], abandoned: threading.Event
) -> McpWriteOutcome:
    st, data = _read_checked(real, label)
    _after_read()
    new = edit_enabled(data, changes)
    if new == data:
        return McpWriteOutcome("unchanged")
    _replace_file(real, label, st, data, new, abandoned)
    return McpWriteOutcome("written")


def _write_authorized(
    target: str | os.PathLike[str],
    real: str,
    label: str,
    changes: Sequence[tuple[str, bool]],
    authorize: Callable[[str], str | None],
    abandoned: threading.Event,
) -> McpWriteOutcome:
    if abandoned.is_set():
        return McpWriteOutcome("abandoned")
    refusal = authorize(real)
    if refusal is not None:
        return McpWriteOutcome("refused", refusal)
    if os.path.realpath(target) != real:
        return McpWriteOutcome("refused", resolves_elsewhere(label))
    return _write_locked(real, label, changes, abandoned)


def resolves_elsewhere(label: str) -> str:
    """The refusal when the target no longer resolves to the real file a save was for."""

    return f"{label} now resolves to another file; nothing written; try again"


def _os_refusal(label: str, exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"{label} is no longer there; nothing written"
    code = errno.errorcode.get(exc.errno or 0)
    why = type(exc).__name__ + (f" {code}" if code else "")
    return f"{label} could not be saved ({why}); nothing written; {_BY_HAND}"


def write_enabled(
    target: str | os.PathLike[str],
    changes: Sequence[tuple[str, bool]],
    *,
    authorize: Callable[[str], str | None],
    abandoned: threading.Event,
    deadline_s: float = LOCK_TIMEOUT_S,
    expected_real: str | None = None,
) -> McpWriteOutcome:
    """Save *changes* to the ``mcp.json`` at *target*, under its lock. Blocking.

    The caller runs it with ``asyncio.to_thread``. ``authorize(real_path)`` is
    called after the lock is taken and before anything is read; a non-None return
    is the refusal text. A refusal is returned, never raised; ``abandoned`` is
    honoured while waiting for the lock, after it, and just before the replace.
    A symlinked target is written through, and the link is kept. *expected_real*
    is the real path the caller read its snapshot from: a target that resolves
    elsewhere when this thread starts is refused before any lock is taken (the
    caller's ``authorize`` compares it again inside the lock).
    """

    label = os.path.basename(os.fspath(target)) or "mcp.json"
    try:
        real = os.path.realpath(target)
        if expected_real is not None and real != expected_real:
            return McpWriteOutcome("refused", resolves_elsewhere(label))
        with _platform_lock(real, deadline_s, abandoned, label):
            return _write_authorized(target, real, label, changes, authorize, abandoned)
    except _Abandoned:
        return McpWriteOutcome("abandoned")
    except McpRefused as exc:
        return McpWriteOutcome("refused", str(exc))
    except OSError as exc:
        return McpWriteOutcome("refused", _os_refusal(label, exc))
