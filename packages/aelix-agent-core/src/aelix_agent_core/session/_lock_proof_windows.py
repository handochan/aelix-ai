"""Local-volume and security-descriptor checks for Windows lock attribution."""

from __future__ import annotations

import contextlib
import ctypes as C
import os
import struct
import sys
from collections.abc import Iterator
from ctypes import wintypes as W
from pathlib import Path
from uuid import UUID

if sys.platform != "win32":
    raise ImportError("Windows session lock proof requires Windows")

_kernel = C.WinDLL("kernel32", use_last_error=True)
_security = C.WinDLL("advapi32", use_last_error=True)
_shell = C.WinDLL("shell32", use_last_error=True)
_com = C.WinDLL("ole32", use_last_error=True)

_PTR = C.c_void_p
_INVALID_HANDLE = _PTR(-1).value
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_TRAVERSE = 0x20
_FILE_READ_ATTRIBUTES = 0x80
_READ_CONTROL = 0x20000
_SHARE_READ_WRITE = 3
_SHARE_ALL = 7
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_OPEN_UNTRAVERSED = 0x02200000  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
_KF_FLAG_DEFAULT_PATH = 0x400
_LOCAL_APP_DATA = UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091")
_SYSTEM = "S-1-5-18"
_ADMINISTRATORS = "S-1-5-32-544"
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_STORE = "aelix-session-locks-v2"

# Other principals may read or traverse checked non-private ancestors.
# Exclude replacement, reparse and security descriptor changes. On a
# directory, adding a sibling does not modify the opened child.
_REDIRECT = (
    0x40 | 0x10 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
)  # DELETE_CHILD | WRITE_EA | WRITE_ATTRIBUTES | DELETE | WRITE_DAC | WRITE_OWNER | generics


class _FileId(C.Structure):
    _fields_ = [("volume", C.c_uint64), ("file_id", C.c_ubyte * 16)]


class _SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", _PTR), ("inherit", W.BOOL)]


class _StandardInfo(C.Structure):
    _fields_ = [
        ("allocated", C.c_int64),
        ("length", C.c_int64),
        ("links", W.DWORD),
        ("deleted", C.c_ubyte),
        ("directory", C.c_ubyte),
    ]


def _check(result: object) -> None:
    if not result:
        raise C.WinError(C.get_last_error())


def _bind(dll: C.WinDLL, name: str, result: type | None, *arguments: type):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


_create_file = _bind(
    _kernel, "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, _PTR,
    W.DWORD, W.DWORD, W.HANDLE,
)
_open_process_token = _bind(_security, "OpenProcessToken", W.BOOL, W.HANDLE, W.DWORD, _PTR)
_open_thread_token = _bind(_security, "OpenThreadToken", W.BOOL, W.HANDLE, W.DWORD, W.BOOL, _PTR)
_token_info = _bind(_security, "GetTokenInformation", W.BOOL, W.HANDLE, C.c_int, _PTR, W.DWORD, _PTR)
_current_process = _bind(_kernel, "GetCurrentProcess", W.HANDLE)
_current_thread = _bind(_kernel, "GetCurrentThread", W.HANDLE)
_close = _bind(_kernel, "CloseHandle", W.BOOL, W.HANDLE)
_free = _bind(_kernel, "LocalFree", _PTR, _PTR)
_sid_to_string = _bind(_security, "ConvertSidToStringSidW", W.BOOL, _PTR, _PTR)
_security_info = _bind(
    _security, "GetSecurityInfo", W.DWORD, W.HANDLE, C.c_int, W.DWORD,
    _PTR, _PTR, _PTR, _PTR, _PTR,
)
_sd_control = _bind(_security, "GetSecurityDescriptorControl", W.BOOL, _PTR, _PTR, _PTR)
_get_ace = _bind(_security, "GetAce", W.BOOL, _PTR, W.DWORD, _PTR)
_create_sd = _bind(
    _security, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
    W.LPCWSTR, W.DWORD, _PTR, _PTR,
)
_known_folder = _bind(_shell, "SHGetKnownFolderPath", C.c_long, _PTR, W.DWORD, W.HANDLE, _PTR)
_com_free = _bind(_com, "CoTaskMemFree", None, _PTR)
_create_directory = _bind(_kernel, "CreateDirectoryW", W.BOOL, W.LPCWSTR, _PTR)
_drive_type = _bind(_kernel, "GetDriveTypeW", C.c_uint, W.LPCWSTR)
_final_name = _bind(_kernel, "GetFinalPathNameByHandleW", W.DWORD, W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD)
_file_type = _bind(_kernel, "GetFileType", W.DWORD, W.HANDLE)
_file_info = _bind(_kernel, "GetFileInformationByHandleEx", W.BOOL, W.HANDLE, C.c_int, _PTR, W.DWORD)
_volume_info = _bind(
    _kernel, "GetVolumeInformationByHandleW", W.BOOL,
    W.HANDLE, _PTR, W.DWORD, _PTR, _PTR, _PTR, W.LPWSTR, W.DWORD,
)
_read = _bind(_kernel, "ReadFile", W.BOOL, W.HANDLE, _PTR, W.DWORD, _PTR, _PTR)
_write = _bind(_kernel, "WriteFile", W.BOOL, W.HANDLE, _PTR, W.DWORD, _PTR, _PTR)


def _sid(address: int | C.c_void_p) -> str:
    value = W.LPWSTR()
    _check(_sid_to_string(address, C.byref(value)))
    try:
        return C.wstring_at(value)
    finally:
        _free(value)


@contextlib.contextmanager
def _user_token() -> Iterator[W.HANDLE]:
    token = W.HANDLE()
    if not _open_thread_token(_current_thread(), 0xC, True, C.byref(token)):
        if C.get_last_error() != 1008:  # ERROR_NO_TOKEN
            raise C.WinError(C.get_last_error())
        _check(_open_process_token(_current_process(), 0xC, C.byref(token)))
    try:
        yield token
    finally:
        _close(token)


def _token_sid(token: W.HANDLE) -> str:
    size = W.DWORD()
    _token_info(token, 1, None, 0, C.byref(size))  # TokenUser
    if not 0 < size.value < 4096:
        raise OSError("invalid token user")
    user = C.create_string_buffer(size.value)
    _check(_token_info(token, 1, user, size, C.byref(size)))
    return _sid(C.cast(user, C.POINTER(_PTR))[0])


def _folder(token: W.HANDLE, flags: int) -> Path:
    guid = C.create_string_buffer(_LOCAL_APP_DATA.bytes_le)
    value = W.LPWSTR()
    try:
        if _known_folder(guid, flags, token, C.byref(value)) != 0:
            raise OSError("LocalAppData could not be verified")
        return Path(C.wstring_at(value))
    finally:
        _com_free(value)


def _cache(token: W.HANDLE) -> Path:
    current = _folder(token, 0)
    if current != _folder(token, _KF_FLAG_DEFAULT_PATH):
        raise OSError("redirected LocalAppData")
    return current


def _check_aces(acl: C.c_void_p, trusted: set[str], *, private: bool, directory: bool) -> None:
    if not acl:
        raise OSError("missing lock proof DACL")
    # ACL header has AceCount at offset four. Reject unusual ACE formats,
    # which may carry object GUIDs, claims or conditional rights.
    count = C.cast(acl, C.POINTER(W.WORD))[2]
    for index in range(count):
        address = _PTR()
        _check(_get_ace(acl, index, C.byref(address)))
        type_, flags, size, mask = struct.unpack("<BBHI", C.string_at(address, 8))
        if size < 16 or type_ not in (0, 1) or address.value is None:
            raise OSError("unverifiable lock proof ACE")
        owner = _sid(address.value + 8)
        if private and (owner not in trusted or flags):
            raise OSError("unexpected ACE on private lock proof storage")
        blocked = _REDIRECT | (0 if directory else 6)  # WRITE_DATA | APPEND_DATA
        if type_ == 0 and not flags & 8 and owner not in trusted and mask & blocked:
            raise OSError("untrusted lock proof permissions")


def _check_security(handle: int, user: str, *, private: bool, directory: bool) -> None:
    owner, acl, descriptor = _PTR(), _PTR(), _PTR()
    status = _security_info(
        handle, 1, 5, C.byref(owner), None, C.byref(acl), None, C.byref(descriptor)
    )  # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    if status:
        raise C.WinError(status)
    try:
        trusted = {user, _SYSTEM, _ADMINISTRATORS}
        if not private:
            trusted.add(_TRUSTED_INSTALLER)
        if not owner or _sid(owner) not in ({user} if private else trusted):
            raise OSError("untrusted lock proof owner")
        if private:
            control, revision = W.WORD(), W.DWORD()
            _check(_sd_control(descriptor, C.byref(control), C.byref(revision)))
            if not control.value & 0x1000:  # SE_DACL_PROTECTED
                raise OSError("inheritable lock proof DACL")
        _check_aces(acl, trusted, private=private, directory=directory)
    finally:
        _free(descriptor)


@contextlib.contextmanager
def _private_attributes(user: str) -> Iterator[_SecurityAttributes]:
    descriptor = _PTR()
    sddl = f"O:{user}D:P(A;;FA;;;{user})(A;;FA;;;SY)(A;;FA;;;BA)"
    _check(_create_sd(sddl, 1, C.byref(descriptor), None))
    try:
        yield _SecurityAttributes(C.sizeof(_SecurityAttributes), descriptor, False)
    finally:
        _free(descriptor)


@contextlib.contextmanager
def _open(
    path: Path, *, access: int, share: int = _SHARE_ALL,
    attributes: _SecurityAttributes | None = None,
) -> Iterator[int]:
    handle = _create_file(
        str(path), access, share, None if attributes is None else C.byref(attributes),
        _OPEN_EXISTING if attributes is None else _CREATE_NEW, _OPEN_UNTRAVERSED, None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise C.WinError(C.get_last_error())
    try:
        yield handle
    finally:
        _close(handle)


def _object(handle: int, *, directory: bool) -> None:
    tag = (W.DWORD * 2)()
    _check(_file_info(handle, 9, C.byref(tag), C.sizeof(tag)))  # FileAttributeTagInfo
    if (
        _file_type(handle) != 1  # FILE_TYPE_DISK
        or tag[0] & _FILE_ATTRIBUTE_REPARSE_POINT
        or bool(tag[0] & _FILE_ATTRIBUTE_DIRECTORY) != directory
    ):
        raise OSError("unexpected lock proof object type")


def _checked_volume(handle: int) -> None:
    volume = C.create_unicode_buffer(32)
    flags = W.DWORD()
    _check(_volume_info(handle, None, 0, None, None, C.byref(flags), volume, len(volume)))
    if volume.value not in ("NTFS", "ReFS") or not flags.value & 0x8:
        raise OSError("unverifiable lock proof filesystem")


def _check_path(path: Path, handle: int) -> None:
    resolved = C.create_unicode_buffer(32768)
    size = _final_name(handle, resolved, len(resolved), 0)
    if not 0 < size < len(resolved) or Path(resolved.value.removeprefix("\\\\?\\")) != path:
        raise OSError("redirected lock proof path")


@contextlib.contextmanager
def _directories(path: Path, user: str, *, private: bool = False, create: bool = False):
    if (
        len(path.drive) != 2 or not path.is_absolute()
        or any(":" in part or part == ".." for part in path.parts[1:])
        or _drive_type(path.anchor) != 3  # DRIVE_FIXED
    ):
        raise OSError("proof storage must be on a fixed local drive")
    with contextlib.ExitStack() as stack:
        for part in (*reversed(path.parents), path):
            dedicated = private and part == path
            if dedicated and create:
                with _private_attributes(user) as attributes:
                    if (
                        not _create_directory(str(part), C.byref(attributes))
                        and C.get_last_error() != 183  # ERROR_ALREADY_EXISTS
                    ):
                        raise C.WinError(C.get_last_error())
            # FILE_TRAVERSE participates in Windows sharing checks, so the
            # handle's denied delete sharing pins the checked directory name.
            handle = stack.enter_context(_open(
                part, access=_FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _READ_CONTROL,
                share=_SHARE_READ_WRITE,
            ))
            _object(handle, directory=True)
            _check_path(part, handle)
            if part == Path(path.anchor):
                _checked_volume(handle)
            _check_security(handle, user, private=dedicated, directory=True)
        yield


def _identity(handle: int) -> bytes:
    _object(handle, directory=False)
    info, identity = _StandardInfo(), _FileId()
    _check(_file_info(handle, 1, C.byref(info), C.sizeof(info)))  # FileStandardInfo
    _check(_file_info(handle, 18, C.byref(identity), C.sizeof(identity)))  # FileIdInfo
    if info.links != 1 or info.deleted or not any(identity.file_id):
        raise OSError("unusable lock proof file ID")
    return struct.pack(">BQ16s", 2, identity.volume, bytes(identity.file_id))


def held_identity(fd: int) -> bytes:
    import msvcrt  # noqa: PLC0415

    handle = msvcrt.get_osfhandle(fd)
    _checked_volume(handle)
    return _identity(handle)


@contextlib.contextmanager
def named_identity(path: str) -> Iterator[bytes]:
    slot = Path(path)
    if ":" in slot.name:
        raise OSError("unexpected lock proof stream")
    with _user_token() as token:
        user = _token_sid(token)
    with (
        _directories(slot.parent, user),
        _open(slot, access=_FILE_READ_ATTRIBUTES | _READ_CONTROL) as handle,
    ):
        identity = _identity(handle)
        _check_path(slot, handle)
        _check_security(handle, user, private=False, directory=False)
        yield identity


@contextlib.contextmanager
def _locator_directory(*, create: bool = False) -> Iterator[tuple[Path, str]]:
    with _user_token() as token:
        path, user = _cache(token) / _STORE, _token_sid(token)
    with _directories(path, user, private=True, create=create):
        yield path, user


@contextlib.contextmanager
def read_locator(key: str) -> Iterator[tuple[bytes, bytes]]:
    with (
        _locator_directory() as (path, user),
        _open(path / key, access=_FILE_READ_ATTRIBUTES | _READ_CONTROL | 1) as handle,
    ):
        identity = _identity(handle)
        _check_security(handle, user, private=True, directory=False)
        data, count = C.create_string_buffer(161), W.DWORD()
        _check(_read(handle, data, len(data), C.byref(count), None))
        yield identity, data.raw[:count.value]


def publish_locator(key: str, temp: str, record: bytes) -> None:
    with _locator_directory(create=True) as (path, user), _private_attributes(user) as attributes:
        temporary = path / temp
        created = False
        try:
            with _open(
                temporary, access=_FILE_READ_ATTRIBUTES | _READ_CONTROL | 2, attributes=attributes
            ) as handle:
                created = True
                _identity(handle)
                _check_security(handle, user, private=True, directory=False)
                count = W.DWORD()
                _check(_write(handle, C.create_string_buffer(record), len(record), C.byref(count), None))
                if count.value != len(record):
                    raise OSError("incomplete lock proof locator")
            os.replace(temporary, path / key)
        finally:
            if created:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()
