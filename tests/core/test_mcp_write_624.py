"""#624 (ADR-0305 D13, D14) — the locked ``mcp.json`` write.

``core/mcp_config_edit.write_enabled`` locks the real file's directory (POSIX
``flock``) or a named mutex (Windows), calls the caller's ``authorize`` inside
the lock, checks the target still resolves where it did, refuses what a
replacement cannot keep, reads bytes, carries owner, group, mode bits and the
ACL/xattrs onto a temp file beside it, checks the file is still what it read,
and replaces it.

The lock holders here take the lock with their own code, not the module's: a
``flock`` of the directory, or ``CreateMutexW`` with the name computed here, so
a writer that locked anything else would not wait for them.

Windows: the cases that use only the holder, the CRLF, read-only and barrier
cases, and the two-process race run there too (the race then measures the named
mutex). The DACL cases and the read-only timing are Windows-only; their first
real run is windows-latest CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import os
import random
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.cli.config import load_mcp_config
from aelix_coding_agent.core import mcp_config_edit as m
from aelix_coding_agent.core.mcp_config_edit import (
    LOCK_TIMEOUT_S,
    McpWriteOutcome,
    edit_enabled,
    write_enabled,
)

from tests.env_sandbox import child_env

SECRET = "SECRET-624"
DEFAULT = '{\n  "mcpServers": {\n    "a": {"command": "x"},\n    "b": {"command": "y"}\n  }\n}\n'
A_OFF = DEFAULT.replace('{"command": "x"}', '{"command": "x","enabled": false}')

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX owner/mode/flock/symlink semantics"
)
windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows DACL and file attributes; the first real run is windows-latest CI",
)


def allow(_real: str) -> str | None:
    return None


def _mcp(directory: Path, text: str = DEFAULT, name: str = "mcp.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _write(path: Path, changes: list[tuple[str, bool]], **kwargs: object) -> McpWriteOutcome:
    kwargs.setdefault("authorize", allow)
    kwargs.setdefault("abandoned", threading.Event())
    return write_enabled(path, changes, **kwargs)  # type: ignore[arg-type]


def _no_temp_left(directory: Path) -> bool:
    return not [p.name for p in directory.iterdir() if p.name.endswith(".tmp")]


class PollProbe(threading.Event):
    """An ``abandoned`` event that says when the writer is polling for the lock."""

    def __init__(self) -> None:
        super().__init__()
        self.polled = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        self.polled.set()
        return super().wait(timeout)


# --- an independent lock holder ---------------------------------------------------------

HOLDER = r"""
import hashlib, os, sys, time
real, hold = sys.argv[1], sys.argv[2]
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.ReleaseMutex.argtypes = [wintypes.HANDLE]
    digest = hashlib.sha256(os.path.normcase(real).encode("utf-8", "surrogatepass")).hexdigest()
    handle = k.CreateMutexW(None, False, "Local\\aelix-mcp-" + digest[:32])
    assert k.WaitForSingleObject(handle, 0xFFFFFFFF) in (0, 0x80)
    release = lambda: k.ReleaseMutex(handle)
else:
    import fcntl
    fd = os.open(os.path.dirname(real), os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    release = lambda: fcntl.flock(fd, fcntl.LOCK_UN)
print("ready", flush=True)
if hold == "stdin":
    sys.stdin.readline()
else:
    time.sleep(float(hold))
release()
print("released", flush=True)
"""


@contextlib.contextmanager
def held_lock(path: Path, hold: str = "stdin") -> Iterator[subprocess.Popen[str]]:
    """Another process holding *path*'s lock until :func:`release` (or *hold* seconds)."""

    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", HOLDER, os.path.realpath(path), hold],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        release(proc)


def release(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            assert proc.stdin is not None
            proc.stdin.write("\n")
            proc.stdin.flush()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _in_thread(fn: object) -> tuple[threading.Thread, list[McpWriteOutcome]]:
    out: list[McpWriteOutcome] = []
    thread = threading.Thread(target=lambda: out.append(fn()))  # type: ignore[operator]
    thread.start()
    return thread, out


def test_mutex_name_is_the_normcased_real_path_hash() -> None:
    real = os.path.realpath(os.path.join("some", "Dir", "mcp.json"))
    digest = hashlib.sha256(os.path.normcase(real).encode("utf-8")).hexdigest()
    assert m._mutex_name(real) == "Local\\aelix-mcp-" + digest[:32]
    assert len(m._mutex_name(real)) == len("Local\\aelix-mcp-") + 32
    assert LOCK_TIMEOUT_S == 30.0


# --- inside the lock: authorize, re-resolution, cancel (ADR-0305 D13) -------------------------


def test_trust_revoked_during_the_lock_wait_is_refused(tmp_path: Path) -> None:
    p = _mcp(tmp_path / "proj")
    before = p.read_bytes()
    trusted = {"value": True}
    calls: list[str] = []

    def authorize(real: str) -> str | None:
        calls.append(real)
        return None if trusted["value"] else "this folder is no longer trusted"

    probe = PollProbe()
    with held_lock(p) as holder:
        thread, out = _in_thread(
            lambda: write_enabled(p, [("a", False)], authorize=authorize, abandoned=probe)
        )
        assert probe.polled.wait(15), "the writer never waited for the lock"
        assert calls == [], "authorize ran before the lock was taken"
        trusted["value"] = False
        release(holder)
        thread.join(15)
    assert out == [McpWriteOutcome("refused", "this folder is no longer trusted")]
    assert calls == [os.path.realpath(p)]
    assert p.read_bytes() == before
    assert _no_temp_left(p.parent)


def test_authorize_runs_before_the_read(tmp_path: Path) -> None:
    """What authorize leaves on disk (say, a checkout under the lock) is what gets edited."""

    p = _mcp(tmp_path)

    def authorize(_real: str) -> str | None:
        p.write_bytes(DEFAULT.replace('"y"', '"z"').encode())
        return None

    out = _write(p, [("a", False)], authorize=authorize)
    assert out == McpWriteOutcome("written")
    assert p.read_bytes() == A_OFF.replace('"y"', '"z"').encode()


@posix_only
def test_symlink_retargeted_during_the_wait_is_refused(tmp_path: Path) -> None:
    a, b = _mcp(tmp_path / "A"), _mcp(tmp_path / "B", DEFAULT.replace('"x"', '"q"'))
    before_a, before_b = a.read_bytes(), b.read_bytes()
    link = tmp_path / "mcp.json"
    link.symlink_to(a)
    probe = PollProbe()
    with held_lock(a) as holder:
        thread, out = _in_thread(
            lambda: write_enabled(link, [("a", False)], authorize=allow, abandoned=probe)
        )
        assert probe.polled.wait(15)
        link.unlink()
        link.symlink_to(b)
        release(holder)
        thread.join(15)
    assert out[0].status == "refused"
    assert "now resolves to another file" in out[0].detail
    assert (a.read_bytes(), b.read_bytes()) == (before_a, before_b)


@posix_only
def test_a_symlink_is_written_through_and_kept(tmp_path: Path) -> None:
    real = _mcp(tmp_path / "real")
    link = tmp_path / "proj" / "mcp.json"
    link.parent.mkdir()
    link.symlink_to(real)
    seen: list[str] = []
    out = _write(link, [("a", False)], authorize=lambda r: seen.append(r))
    assert out == McpWriteOutcome("written")
    assert link.is_symlink() and os.readlink(link) == str(real)
    assert real.read_bytes() == A_OFF.encode()
    assert seen == [os.path.realpath(real)]
    assert _no_temp_left(real.parent) and _no_temp_left(link.parent)


def test_a_cancel_during_the_wait_abandons_without_waiting(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    before = p.read_bytes()
    probe = PollProbe()
    calls: list[str] = []
    with held_lock(p):
        thread, out = _in_thread(
            lambda: write_enabled(
                p, [("a", False)], authorize=lambda r: calls.append(r), abandoned=probe
            )
        )
        assert probe.polled.wait(15)
        probe.set()
        thread.join(15)
        assert not thread.is_alive(), "the cancel waited for the lock"
    assert out == [McpWriteOutcome("abandoned")]
    assert calls == []
    assert p.read_bytes() == before


def test_a_cancel_set_before_the_lock_is_honoured_after_it(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    calls: list[str] = []
    out = _write(p, [("a", False)], authorize=lambda r: calls.append(r), abandoned=cancelled)
    assert out == McpWriteOutcome("abandoned")
    assert calls == []
    assert p.read_bytes() == DEFAULT.encode()


def test_a_cancel_just_before_the_replace_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    cancelled = threading.Event()
    real_carry = m._carry_from

    def carry_then_cancel(*args: object) -> None:
        real_carry(*args)  # type: ignore[arg-type]
        cancelled.set()

    monkeypatch.setattr(m, "_carry_from", carry_then_cancel)
    out = _write(p, [("a", False)], abandoned=cancelled)
    assert out == McpWriteOutcome("abandoned")
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def test_the_lock_deadline_refuses(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    with held_lock(p):
        started = time.monotonic()
        out = _write(p, [("a", False)], deadline_s=0.3)
        waited = time.monotonic() - started
    assert out.status == "refused"
    assert "another aelix has been saving mcp.json" in out.detail
    assert 0.25 <= waited < 10
    assert p.read_bytes() == DEFAULT.encode()


def test_a_held_lock_keeps_the_event_loop_ticking(tmp_path: Path) -> None:
    p = _mcp(tmp_path)

    async def run() -> tuple[McpWriteOutcome, list[float]]:
        task = asyncio.ensure_future(
            asyncio.to_thread(
                write_enabled, p, [("a", False)], authorize=allow, abandoned=threading.Event()
            )
        )
        gaps: list[float] = []
        last = time.monotonic()
        while not task.done():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
        return await task, gaps

    with held_lock(p, hold="1.0"):
        started = time.monotonic()
        outcome, gaps = asyncio.run(run())
        waited = time.monotonic() - started
    print(
        f"largest tick gap {max(gaps) * 1000:.0f} ms over {len(gaps)} ticks, waited {waited:.2f} s"
    )
    assert outcome == McpWriteOutcome("written")
    assert waited >= 0.7, "the write did not wait for the held lock"
    assert max(gaps) < 0.5
    assert p.read_bytes() == A_OFF.encode()


RACER = r"""
import sys, threading, time
from aelix_coding_agent.core import mcp_config_edit as m
path, prefix, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
m._after_read = lambda: time.sleep(0.005)
print("ready", flush=True)
sys.stdin.readline()
start = time.time()
bad = []
for i in range(n):
    out = m.write_enabled(path, [(f"{prefix}{i}", False)], authorize=lambda _r: None,
                          abandoned=threading.Event())
    if out.status != "written":
        bad.append(out.status + ": " + out.detail)
print(f"done {start!r} {time.time()!r} {len(bad)}", flush=True)
for line in bad:
    print(line, flush=True)
"""


def test_two_processes_with_two_agent_dirs_lose_no_update(tmp_path: Path) -> None:
    servers = {f"{pre}{i}": {"command": "x"} for pre in ("a", "b") for i in range(20)}
    p = _mcp(tmp_path / "shared", json.dumps({"mcpServers": servers}, indent=2) + "\n")
    procs = []
    for pre in ("a", "b"):
        env = child_env(
            tmp_path / f"home-{pre}", AELIX_CODING_AGENT_DIR=str(tmp_path / f"agent-{pre}")
        )
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", RACER, str(p), pre, "20"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=tmp_path,
            )
        )
    try:
        for proc in procs:
            assert proc.stdout is not None and proc.stdin is not None
            line = proc.stdout.readline()
            assert line.strip() == "ready", line + proc.stdout.read()
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        outputs = [proc.communicate(timeout=180)[0] for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    spans = []
    for text in outputs:
        done = text.splitlines()[0].split()
        assert done[0] == "done" and done[3] == "0", text
        spans.append((float(done[1]), float(done[2])))
    assert max(a for a, _ in spans) < min(b for _, b in spans), "the two loops never overlapped"
    final = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]
    lost = sorted(name for name, entry in final.items() if entry.get("enabled", True))
    assert lost == []
    assert _no_temp_left(p.parent)


# --- refused up front, nothing written (opus L4) -----------------------------------------------


def test_read_only_file_is_refused(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    os.chmod(p, 0o444)
    try:
        out = _write(p, [("a", False)])
    finally:
        os.chmod(p, 0o644)
    assert out.status == "refused"
    assert out.detail == "mcp.json is read-only; edit it by hand"
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@posix_only
def test_owner_write_bit_off_is_refused_even_when_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root may write a 0o444 file; the owner's bit is the user's own "read-only"."""

    p = _mcp(tmp_path)
    os.chmod(p, 0o444)
    monkeypatch.setattr(m.os, "access", lambda *_a, **_k: True)
    try:
        out = _write(p, [("a", False)])
    finally:
        os.chmod(p, 0o644)
    assert out.detail == "mcp.json is read-only; edit it by hand"


@posix_only
def test_a_hard_link_is_refused(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    os.link(p, tmp_path / "other.json")
    out = _write(p, [("a", False)])
    assert out.status == "refused"
    assert "another hard link" in out.detail and out.detail.endswith("edit it by hand")
    assert p.read_bytes() == DEFAULT.encode()


@posix_only
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_not_a_regular_file_is_refused(tmp_path: Path, kind: str) -> None:
    p = tmp_path / "mcp.json"
    if kind == "fifo":
        os.mkfifo(p)
    else:
        p.mkdir()
    out = _write(p, [("a", False)])
    assert out.status == "refused"
    assert out.detail.startswith("mcp.json")
    assert sorted(x.name for x in tmp_path.iterdir()) == ["mcp.json"]


def test_a_missing_file_is_refused_and_not_created(tmp_path: Path) -> None:
    out = _write(tmp_path / "mcp.json", [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json is no longer there; nothing written")
    assert list(tmp_path.iterdir()) == []


def test_an_os_error_is_a_value_free_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)

    def full(*_args: object) -> None:
        raise OSError(28, f"No space left on device: {SECRET}")

    monkeypatch.setattr(m, "_write_all", full)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome(
        "refused", "mcp.json could not be saved (OSError ENOSPC); nothing written; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def test_nothing_to_change_is_unchanged_and_not_replaced(tmp_path: Path) -> None:
    p = _mcp(tmp_path, A_OFF)
    before = p.stat()
    out = _write(p, [("a", False), ("b", True)])
    assert out == McpWriteOutcome("unchanged")
    after = p.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_an_edit_refusal_is_returned_not_raised(tmp_path: Path) -> None:
    p = _mcp(tmp_path, '{"mcpServers": {"a": {"command": "x", "enabled": "on"}}}')
    out = _write(p, [("a", False)])
    assert out.status == "refused"
    assert out.detail == 'server "a": "enabled" is not true or false; edit the file by hand'


# --- what a replacement keeps ----------------------------------------------------------------


def test_crlf_is_kept_byte_for_byte(tmp_path: Path) -> None:
    text = '{\r\n  "mcpServers": {\r\n    "a": {\r\n      "command": "x"\r\n    }\r\n  }\r\n}\r\n'
    p = _mcp(tmp_path, text)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert p.read_bytes() == text.replace('"x"\r\n', '"x",\r\n      "enabled": false\r\n').encode()


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o604])
def test_mode_bits_are_kept(tmp_path: Path, mode: int) -> None:
    p = _mcp(tmp_path)
    os.chmod(p, mode)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert stat.S_IMODE(p.stat().st_mode) == mode


def _second_group(p: Path) -> int | None:
    groups = [g for g in os.getgroups() if g != p.stat().st_gid]
    return groups[0] if groups else None


@posix_only
def test_the_group_is_kept(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    group = _second_group(p)
    if group is None:
        pytest.skip("this process has no second group")
    os.chown(p, -1, group)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert p.stat().st_gid == group


@posix_only
def test_an_owner_or_group_that_cannot_be_kept_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    group = _second_group(p)
    if group is None:
        pytest.skip("this process has no second group")
    os.chown(p, -1, group)

    def refuse(*_args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(m.os, "fchown", refuse)
    out = _write(p, [("a", False)])
    assert (
        out.detail == "mcp.json's owner or group cannot be kept on a replacement; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@posix_only
def test_mode_bits_that_do_not_stick_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    os.chmod(p, 0o640)
    monkeypatch.setattr(m.os, "fchmod", lambda *_a: None)
    out = _write(p, [("a", False)])
    assert out.detail == (
        "mcp.json's owner, group or mode bits cannot be kept on a replacement; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def _posix_acl(uid: int) -> bytes:
    """A ``system.posix_acl_access`` value (version 2): user::rw-, user:<uid>:r--,
    group::r--, mask::r--, other::---."""

    import struct

    undefined = 0xFFFFFFFF
    entries = [(0x01, 6, undefined), (0x02, 4, uid), (0x04, 4, undefined), (0x10, 4, undefined)]
    entries.append((0x20, 0, undefined))
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, q, i) for t, q, i in entries)


def _linux_xattrs_or_skip(p: Path) -> None:
    try:
        os.setxattr(p, "user.aelix-probe", b"1")
        os.removexattr(p, "user.aelix-probe")
        os.setxattr(p, "system.posix_acl_access", _posix_acl(1234))
        os.removexattr(p, "system.posix_acl_access")
    except OSError as exc:
        pytest.skip(f"this filesystem refuses xattrs or ACLs ({exc.errno})")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux xattrs and POSIX ACLs")
def test_linux_posix_acl_and_user_xattr_are_kept(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    _linux_xattrs_or_skip(p)
    acl = _posix_acl(1234)
    os.setxattr(p, "system.posix_acl_access", acl)
    os.setxattr(p, "user.k", b"v")
    mode = stat.S_IMODE(p.stat().st_mode)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert os.getxattr(p, "system.posix_acl_access") == acl
    assert os.getxattr(p, "user.k") == b"v"
    assert stat.S_IMODE(p.stat().st_mode) == mode
    assert p.read_bytes() == A_OFF.encode()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux default ACLs")
@pytest.mark.parametrize("own_acl", [False, True])
def test_linux_an_acl_the_temp_inherits_is_not_kept(tmp_path: Path, own_acl: bool) -> None:
    """The directory's default ACL gives the temp file an ACL: dropped when the file had
    none, replaced by the file's own when it had a different one."""

    p = _mcp(tmp_path / "d")
    _linux_xattrs_or_skip(p)
    if own_acl:
        os.setxattr(p, "system.posix_acl_access", _posix_acl(4321))
    os.setxattr(p.parent, "system.posix_acl_default", _posix_acl(1234))
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    if own_acl:
        assert os.getxattr(p, "system.posix_acl_access") == _posix_acl(4321)
    else:
        assert "system.posix_acl_access" not in os.listxattr(p)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux xattrs")
def test_linux_an_xattr_that_cannot_be_kept_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    _linux_xattrs_or_skip(p)
    os.setxattr(p, "user.k", b"v")

    def refuse(*_args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(m.os, "setxattr", refuse)
    out = _write(p, [("a", False)])
    assert out.detail == (
        "mcp.json's ACL or extended attributes cannot be kept on a replacement; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux xattrs")
def test_linux_a_filesystem_without_xattrs_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)

    def unsupported(*_args: object) -> list[str]:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(m.os, "listxattr", unsupported)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert p.read_bytes() == A_OFF.encode()


def _ls_le(p: Path) -> str:
    return subprocess.run(
        ["ls", "-le", str(p)], capture_output=True, encoding="utf-8", errors="replace", check=True
    ).stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_macos_acl_is_kept(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    subprocess.run(["chmod", "+a", "nobody deny read,write", str(p)], check=True)
    try:
        assert _write(p, [("a", False)]) == McpWriteOutcome("written")
        listing = _ls_le(p)
    finally:
        subprocess.run(["chmod", "-N", str(p)], check=True)
    # Under load ls -le may print nobody's GUID instead of the name (seen 1 in 10 runs).
    assert re.search(
        r"(user:nobody|FFFFEEEE-DDDD-CCCC-BBBB-AAAAFFFFFFFE) deny read,write", listing
    ), listing
    assert p.read_bytes() == A_OFF.encode()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_macos_deny_delete_acl_is_refused_and_the_temp_removed(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    subprocess.run(["chmod", "+a", "everyone deny delete", str(p)], check=True)
    try:
        started = time.monotonic()
        out = _write(p, [("a", False)])
        elapsed = time.monotonic() - started
        left = sorted(x.name for x in tmp_path.iterdir())
    finally:
        subprocess.run(["chmod", "-N", str(p)], check=True)
    assert out.status == "refused"
    assert elapsed < 1.0, "POSIX does not retry a refused replace"
    assert out.detail.startswith("mcp.json cannot be replaced")
    assert left == ["mcp.json"]
    assert p.read_bytes() == DEFAULT.encode()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS fcopyfile")
def test_macos_acl_that_cannot_be_kept_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    monkeypatch.setattr(m, "_darwin_libc", lambda: None)
    out = _write(p, [("a", False)])
    assert out.detail == (
        "mcp.json's ACL or extended attributes cannot be kept on a replacement; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


# --- the file changed between the read and the replace (a barrier) -----------------------------


@pytest.mark.parametrize(
    "how", ["replaced", "replaced-same-bytes", "appended", "same-size-same-mtime"]
)
def test_a_change_after_the_read_is_refused_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    p = _mcp(tmp_path)
    other = DEFAULT.replace('"y"', '"z"').encode()

    def other_writer() -> None:
        if how.startswith("replaced"):
            (tmp_path / "editor.tmp").write_bytes(DEFAULT.encode() if "same" in how else other)
            os.replace(tmp_path / "editor.tmp", p)
        elif how == "appended":
            with open(p, "ab") as fh:
                fh.write(b"\n")
        else:  # a coarse mtime: same size, same timestamps, other bytes
            st = p.stat()
            with open(p, "r+b") as fh:
                fh.write(other)
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))

    monkeypatch.setattr(m, "_after_read", other_writer)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome(
        "refused", "mcp.json changed while it was being saved; nothing written; try again"
    )
    expected = {"appended": DEFAULT.encode() + b"\n", "replaced-same-bytes": DEFAULT.encode()}
    expected = expected.get(how, other)
    assert p.read_bytes() == expected
    assert _no_temp_left(tmp_path)


def test_a_change_after_the_temp_is_written_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    real_carry = m._carry_from

    def carry_then_other_writer(*args: object) -> None:
        real_carry(*args)  # type: ignore[arg-type]
        st = p.stat()
        with open(p, "r+b") as fh:
            fh.write(DEFAULT.replace('"y"', '"z"').encode())
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))

    monkeypatch.setattr(m, "_carry_from", carry_then_other_writer)
    out = _write(p, [("a", False)])
    assert out.status == "refused" and "changed while it was being saved" in out.detail
    assert p.read_bytes() == DEFAULT.replace('"y"', '"z"').encode()
    assert _no_temp_left(tmp_path)


# --- no secret printed ------------------------------------------------------------------------

SECRET_DOC = {
    "mcpServers": {
        "stdio": {"command": "npx", "args": ["--token", SECRET], "env": {"TOKEN": SECRET}},
        "web": {
            "url": f"https://h/{SECRET}/mcp?token={SECRET}",
            "headers": {"Authorization": f"Bearer {SECRET}"},
        },
        "bad": {"command": "x", "enabled": SECRET},
    }
}


def test_refusal_texts_never_carry_a_value(tmp_path: Path) -> None:
    text = json.dumps(SECRET_DOC, indent=2)
    details = []
    p = _mcp(tmp_path / "plain", text)
    details.append(_write(p, [("bad", False)]))  # a non-boolean enabled
    details.append(_write(p, [("gone", False)]))  # no such server
    for broken in (
        text[:-10],  # truncated mid-value
        text.replace('"TOKEN"', '"N": 1, "N"'),  # a duplicate key beside a secret
        text.replace(f'"{SECRET}"', "NaN", 1),
        text.replace(SECRET, SECRET + "\x01", 1),  # a control character in a string
    ):
        details.append(_write(_mcp(tmp_path / f"b{len(details)}", broken), [("stdio", False)]))
    if sys.platform != "win32":
        linked = _mcp(tmp_path / "linked", text)
        os.link(linked, linked.parent / "other")
        details.append(_write(linked, [("stdio", False)]))
    ro = _mcp(tmp_path / "ro", text)
    os.chmod(ro, 0o444)
    details.append(_write(ro, [("stdio", False)]))
    os.chmod(ro, 0o644)
    assert all(out.status == "refused" for out in details), details
    for out in details:
        assert SECRET not in out.detail
    written = _write(p, [("stdio", False), ("web", False)])
    assert written == McpWriteOutcome("written")


# --- the loader agrees ------------------------------------------------------------------------


def _loader_view(
    path: Path, monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> tuple[set[str], set[str], list[str]]:
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(path))
    config = load_mcp_config(str(cwd))
    return {c.name for c in config.contribs}, {c.name for c in config.disabled}, config.warnings


def _check_loader_agrees(
    path: Path, changes: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch, cwd: Path
) -> None:
    on_before, off_before, warnings_before = _loader_view(path, monkeypatch, cwd)
    out = _write(path, changes)
    assert out.status in ("written", "unchanged"), out
    on_after, off_after, warnings_after = _loader_view(path, monkeypatch, cwd)
    assert warnings_after == warnings_before
    asked = dict(changes)
    for name in on_before | off_before:
        want = asked.get(name, name in on_before)
        assert (name in on_after, name in off_after) == (want, not want), name


@pytest.mark.parametrize(
    "text",
    [
        DEFAULT,
        '{"mcpServers":{"a":{"command":"x","enabled":false},"b":{"url":"https://h"}}}',
        '{\r\n\t"mcpServers": {\r\n\t\t"a": {\r\n\t\t\t"command": "x",\r\n\t\t\t"enabled": true\r\n\t\t},'
        '\r\n\t\t"b": {"command": "y", "env": {"T": "SECRET-624"}}\r\n\t}\r\n}',
        '{"mcpServers": {"a": {\n  "env": {\n    "K": "v"\n  }, "command": "x"\n}, '
        '"b": {"command": "y", "enabled": "SECRET-624"}}}\n',
    ],
)
@pytest.mark.parametrize(
    "changes",
    [[("a", False)], [("a", True)], [("a", False), ("b", False)], [("a", True), ("b", True)]],
)
def test_the_loader_agrees_with_each_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, changes: list[tuple[str, bool]]
) -> None:
    p = _mcp(tmp_path / "cfg", text)
    if '"enabled": "SECRET-624"' in text and any(name == "b" for name, _want in changes):
        # b's "enabled" is not a boolean: the edit refuses it, as the loader skips it.
        assert _write(p, changes).status == "refused"
        assert p.read_bytes() == text.encode()
        return
    _check_loader_agrees(p, changes, monkeypatch, tmp_path)


def test_the_loader_agrees_on_re_laid_out_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.core.test_mcp_config_edit_624 import generated_case

    rng = random.Random(6244)
    for i in range(200):
        text, _doc, changes = generated_case(rng, "relayout")
        p = _mcp(tmp_path / f"g{i}", text)
        on_before, off_before, warnings_before = _loader_view(p, monkeypatch, tmp_path)
        p.write_bytes(edit_enabled(p.read_bytes(), changes))
        on_after, off_after, warnings_after = _loader_view(p, monkeypatch, tmp_path)
        assert warnings_after == warnings_before
        asked = dict(changes)
        for name in on_before | off_before:
            want = asked.get(name, name in on_before)
            assert (name in on_after, name in off_after) == (want, not want), (i, name)


# --- Windows: the DACL rule and the read-only attribute (first real run: windows-latest CI) ----


def _icacls(*args: str) -> str:
    return subprocess.run(
        ["icacls", *args], capture_output=True, encoding="utf-8", errors="replace", check=True
    ).stdout


@windows_only
def test_windows_explicit_ace_is_refused(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    _icacls(str(p), "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:F")
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@windows_only
def test_windows_inherited_only_file_is_written_with_the_same_acl(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    before = _icacls(str(p))
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert _icacls(str(p)) == before
    assert p.read_bytes() == A_OFF.encode()


@windows_only
def test_windows_read_only_attribute_is_refused_at_once(tmp_path: Path) -> None:
    p = _mcp(tmp_path)
    os.chmod(p, stat.S_IREAD)
    try:
        assert p.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        started = time.monotonic()
        out = _write(p, [("a", False)])
        elapsed = time.monotonic() - started
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    assert out.detail == "mcp.json is read-only; edit it by hand"
    assert elapsed < 1.0, "the read-only file went to the replace retry"
    assert _no_temp_left(tmp_path)


# --- fix-r1: what may read the file is checked again before the replace (Codex CX-1) ---------
#
# The identity the writer records at the read is device, inode, size, mtime, the
# whole st_mode, owner, group and ctime. ctime moves on every metadata change (a
# chmod, a chown, an ACL or an xattr), so a change made between the read and the
# replace is refused instead of being undone by a replacement carrying the old
# metadata.

CHANGED = McpWriteOutcome(
    "refused", "mcp.json changed while it was being saved; nothing written; try again"
)
IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_ctime_ns",
)


@pytest.mark.parametrize("field", IDENTITY_FIELDS)
def test_the_identity_holds_each_field(field: str) -> None:
    """Each field the writer re-checks is in the identity it compares (one at a time)."""

    from types import SimpleNamespace

    base = dict.fromkeys(IDENTITY_FIELDS, 1)
    changed = dict(base, **{field: 2})
    assert m._identity(SimpleNamespace(**base)) != m._identity(SimpleNamespace(**changed))  # type: ignore[arg-type]


def _metadata_change_after_the_read(monkeypatch: pytest.MonkeyPatch, change: object) -> None:
    monkeypatch.setattr(m, "_after_read", change)


@posix_only
def test_a_chmod_after_the_read_is_refused_and_not_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0644 read, tightened to 0600 before the replace: refused; the file stays 0600."""

    p = _mcp(tmp_path)
    os.chmod(p, 0o644)
    _metadata_change_after_the_read(monkeypatch, lambda: os.chmod(p, 0o600))
    assert _write(p, [("a", False)]) == CHANGED
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@posix_only
def test_a_chmod_to_the_same_mode_after_the_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ctime moves (mode, owner, size and mtime stay): still refused."""

    p = _mcp(tmp_path)
    os.chmod(p, 0o644)
    before = p.stat()

    def same_mode() -> None:
        time.sleep(0.01)  # a ctime tick on a coarse clock
        os.chmod(p, 0o644)
        after = p.stat()
        assert after.st_ctime_ns != before.st_ctime_ns, "the platform did not move ctime"
        assert (after.st_mode, after.st_mtime_ns) == (before.st_mode, before.st_mtime_ns)

    _metadata_change_after_the_read(monkeypatch, same_mode)
    assert _write(p, [("a", False)]) == CHANGED
    assert p.read_bytes() == DEFAULT.encode()


@posix_only
def test_a_group_change_after_the_read_is_refused_and_not_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    group = _second_group(p)
    if group is None:
        pytest.skip("this process has no second group")
    _metadata_change_after_the_read(monkeypatch, lambda: os.chown(p, -1, group))
    assert _write(p, [("a", False)]) == CHANGED
    assert p.stat().st_gid == group
    assert p.read_bytes() == DEFAULT.encode()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_macos_an_acl_added_after_the_read_is_refused_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)

    def add_acl() -> None:
        subprocess.run(["chmod", "+a", "nobody deny read", str(p)], check=True)

    _metadata_change_after_the_read(monkeypatch, add_acl)
    try:
        out = _write(p, [("a", False)])
        listing = _ls_le(p)
    finally:
        subprocess.run(["chmod", "-N", str(p)], check=True)
    assert out == CHANGED
    assert "user:nobody deny read" in listing
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS xattr tool")
def test_macos_an_xattr_added_after_the_read_is_refused_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)

    def add_xattr() -> None:
        subprocess.run(["xattr", "-w", "user.aelix-624", "kept", str(p)], check=True)

    _metadata_change_after_the_read(monkeypatch, add_xattr)
    assert _write(p, [("a", False)]) == CHANGED
    shown = subprocess.run(
        ["xattr", "-p", "user.aelix-624", str(p)], capture_output=True, text=True, check=True
    )
    assert shown.stdout.strip() == "kept"
    assert p.read_bytes() == DEFAULT.encode()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux xattrs")
def test_linux_an_xattr_added_after_the_read_is_refused_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    _linux_xattrs_or_skip(p)
    _metadata_change_after_the_read(monkeypatch, lambda: os.setxattr(p, "user.aelix-624", b"kept"))
    assert _write(p, [("a", False)]) == CHANGED
    assert os.getxattr(p, "user.aelix-624") == b"kept"
    assert p.read_bytes() == DEFAULT.encode()


def test_a_normal_save_leaves_the_identity_check_passing(tmp_path: Path) -> None:
    """The read and the carry step do not move the original's ctime themselves."""

    p = _mcp(tmp_path)
    if sys.platform != "win32":
        os.chmod(p, 0o640)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert p.read_bytes() == A_OFF.encode()


# --- fix-r1: every replace retry re-checks the file first (Codex CX-4) ---------------------


def test_each_replace_retry_rechecks_the_file_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows retry path, switched on here: a sharing violation, then an editor's
    write, then the retry. The retry finds the file changed and writes nothing."""

    p = _mcp(tmp_path)
    other = DEFAULT.replace('"y"', '"z"').encode()
    attempts: list[str] = []
    real_replace = os.replace

    def sharing_violation_then_an_editor(src: object, dst: object) -> None:
        attempts.append(str(dst))
        if len(attempts) == 1:
            (tmp_path / "editor.tmp").write_bytes(other)
            real_replace(tmp_path / "editor.tmp", p)
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(m, "_RETRY_REPLACE", True)
    monkeypatch.setattr(m.os, "replace", sharing_violation_then_an_editor)
    out = _write(p, [("a", False)])
    assert out == CHANGED
    assert attempts == [os.path.realpath(p)], "the retry replaced before checking the file"
    assert p.read_bytes() == other
    assert _no_temp_left(tmp_path)


def test_a_retry_after_a_sharing_violation_still_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    attempts: list[str] = []
    real_replace = os.replace

    def sharing_violation_once(src: object, dst: object) -> None:
        attempts.append(str(dst))
        if len(attempts) == 1:
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(m, "_RETRY_REPLACE", True)
    monkeypatch.setattr(m.os, "replace", sharing_violation_once)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert len(attempts) == 2
    assert p.read_bytes() == A_OFF.encode()


# --- fix-r1: Windows compares the replacement's owner, group and DACL (Codex CX-2) ----------
#
# The comparison runs on Windows only; here it is switched on with a stubbed
# descriptor reader, so the refusal and the write path run on every platform.


def _descriptors(
    monkeypatch: pytest.MonkeyPatch, original: str | None, replacement: str | None
) -> list[str]:
    read: list[str] = []

    def reader(path: str) -> str | None:
        read.append(os.path.basename(path))
        return original if os.path.basename(path) == "mcp.json" else replacement

    monkeypatch.setattr(m, "_COMPARE_DESCRIPTORS", True)
    monkeypatch.setattr(m, "_descriptor", reader)
    return read


def test_a_replacement_with_another_owner_group_or_dacl_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    read = _descriptors(
        monkeypatch,
        "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)",
        "O:S-1-5-21-2G:S-1-5-21-513D:AI(A;ID;FA;;;SY)",
    )
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert "mcp.json" in read and any(name.endswith(".tmp") for name in read)
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def test_a_replacement_with_the_same_owner_group_and_dacl_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    same = "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)"
    read = _descriptors(monkeypatch, same, same)
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    # Compared twice, each time original then temp: on the empty temp, then before the replace.
    assert len(read) == 4 and read[0::2] == ["mcp.json", "mcp.json"], read
    assert p.read_bytes() == A_OFF.encode()


def test_a_descriptor_that_cannot_be_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    _descriptors(monkeypatch, "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)", None)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome(
        "refused", "mcp.json's access rules cannot be read; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@windows_only
def test_windows_an_ordinary_file_and_a_temp_beside_it_have_one_descriptor(tmp_path: Path) -> None:
    """The premise of the Windows rule, on the runner's own folders.

    The descriptors are printed as a warning (they name only SIDs) so that the CI log
    shows them whether or not the comparison holds.
    """

    p = _mcp(tmp_path)
    fd, tmp = tempfile.mkstemp(dir=tmp_path, prefix=".mcp.json.", suffix=".tmp")
    os.close(fd)
    seen = {
        "folder": m._descriptor(str(tmp_path)),
        "file": m._descriptor(str(p)),
        "temp": m._descriptor(tmp),
    }
    warnings.warn(f"windows descriptors: {seen}", stacklevel=1)
    with contextlib.suppress(OSError):
        os.unlink(tmp)
    assert seen["file"] is not None and seen["file"] == seen["temp"], seen


def test_access_is_compared_on_the_empty_temp_before_the_content_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file whose access already differs from what a replacement would get never has
    the new content (its credentials included) written into a temp beside it (verify of
    the CI fix, F1)."""

    secret = DEFAULT.replace('"y"}', '"y", "env": {"T": "SECRET-624"}}')
    p = _mcp(tmp_path, secret)
    sizes: list[int] = []

    def reader(path: str) -> str | None:
        if os.path.basename(path) == "mcp.json":
            return "O:S-1-5-21-1G:S-1-5-21-513D:P(A;;FA;;;SY)"
        sizes.append(os.path.getsize(path))
        return "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)"

    monkeypatch.setattr(m, "_COMPARE_DESCRIPTORS", True)
    monkeypatch.setattr(m, "_descriptor", reader)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert sizes == [0], "the temp held content when its access was first compared"
    assert p.read_bytes() == secret.encode()
    assert _no_temp_left(tmp_path)


def test_an_original_whose_descriptor_cannot_be_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _mcp(tmp_path)
    _descriptors(monkeypatch, None, "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)")
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome(
        "refused", "mcp.json's access rules cannot be read; edit it by hand"
    )
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def test_access_is_compared_again_before_each_replace_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows retry path, switched on here: a sharing violation, then the original's
    access changes (its bytes do not), then the retry. The retry refuses and writes nothing."""

    p = _mcp(tmp_path)
    same = "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)"
    original = [same]
    attempts: list[str] = []

    def reader(path: str) -> str | None:
        return original[0] if os.path.basename(path) == "mcp.json" else same

    def sharing_violation_then_a_new_acl(src: object, dst: object) -> None:
        attempts.append(str(dst))
        original[0] = "O:S-1-5-21-1G:S-1-5-21-513D:P(A;;FA;;;SY)"
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(m, "_COMPARE_DESCRIPTORS", True)
    monkeypatch.setattr(m, "_descriptor", reader)
    monkeypatch.setattr(m, "_RETRY_REPLACE", True)
    monkeypatch.setattr(m.os, "replace", sharing_violation_then_a_new_acl)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert len(attempts) == 1, "the retry replaced before comparing the access again"
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


def test_a_refused_temp_is_closed_before_it_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows' sharing rule, emulated everywhere: deleting the temp while the mkstemp
    handle is open fails, so a refusal must close the handle first (CI-fix verify, F8)."""

    p = _mcp(tmp_path)
    opened: dict[str, int] = {}
    closed: set[int] = set()
    real_mkstemp, real_close, real_unlink = tempfile.mkstemp, os.close, os.unlink

    def mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)  # type: ignore[call-overload]
        opened[name] = fd
        closed.discard(fd)  # descriptor numbers are reused
        return fd, name

    def close(fd: int) -> None:
        closed.add(fd)
        real_close(fd)

    def unlink(path: object, *args: object, **kwargs: object) -> None:
        fd = opened.get(os.fspath(path))  # type: ignore[arg-type]
        if fd is not None and fd not in closed:
            raise PermissionError(32, "The process cannot access the file")
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    _descriptors(
        monkeypatch,
        "O:S-1-5-21-1G:S-1-5-21-513D:P(A;;FA;;;SY)",
        "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)",
    )
    monkeypatch.setattr(m.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(m.os, "close", close)
    monkeypatch.setattr(m.os, "unlink", unlink)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert opened, "the writer made no temp"
    assert _no_temp_left(tmp_path)


def test_a_temp_whose_close_fails_is_still_deleted_and_the_refusal_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the refused temp fails: the refusal is still what the save says, and the
    temp is still deleted (CI-fix verify r3, F15)."""

    p = _mcp(tmp_path)
    temps: set[int] = set()
    real_mkstemp, real_close = tempfile.mkstemp, os.close

    def mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)  # type: ignore[call-overload]
        temps.add(fd)
        return fd, name

    def close(fd: int) -> None:
        real_close(fd)
        if fd in temps:
            temps.discard(fd)
            raise OSError(errno.EIO, "close failed")

    _descriptors(
        monkeypatch,
        "O:S-1-5-21-1G:S-1-5-21-513D:P(A;;FA;;;SY)",
        "O:S-1-5-21-1G:S-1-5-21-513D:AI(A;ID;FA;;;SY)",
    )
    monkeypatch.setattr(m.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(m.os, "close", close)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json has its own access rules; edit it by hand")
    assert _no_temp_left(tmp_path)


@windows_only
def test_windows_the_replacement_keeps_owner_group_and_dacl(tmp_path: Path) -> None:
    """Windows-only, real descriptors: the plain inherited case compares equal and is written."""

    p = _mcp(tmp_path)
    before = m._descriptor(str(p))
    assert before is not None and before.startswith("O:") and "D:" in before
    assert _write(p, [("a", False)]) == McpWriteOutcome("written")
    assert m._descriptor(str(p)) == before
    assert p.read_bytes() == A_OFF.encode()


# --- fix-r1: "this process may not write it" (verifier F2, mutant M09) -----------------------


def test_a_file_this_process_may_not_write_is_refused_whatever_its_mode_bits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner write bit is on; os.access says no (an ACL, a read-only mount)."""

    p = _mcp(tmp_path)
    real = os.path.realpath(p)
    real_access = os.access

    def deny_write(path: object, mode: int, *args: object, **kwargs: object) -> bool:
        if os.fspath(path) == real and mode == os.W_OK:  # type: ignore[arg-type]
            return False
        return real_access(path, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(m.os, "access", deny_write)
    out = _write(p, [("a", False)])
    assert out == McpWriteOutcome("refused", "mcp.json is read-only; edit it by hand")
    assert p.read_bytes() == DEFAULT.encode()
    assert _no_temp_left(tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_macos_an_acl_that_denies_this_user_write_is_refused(tmp_path: Path) -> None:
    """The owner bit stays on; ``user:<me> deny write`` is what refuses (os.access)."""

    import getpass

    p = _mcp(tmp_path)
    entry = f"user:{getpass.getuser()} deny write"
    subprocess.run(["chmod", "+a", entry, str(p)], check=True)
    try:
        assert p.stat().st_mode & stat.S_IWUSR
        assert not os.access(p, os.W_OK)
        out = _write(p, [("a", False)])
        after = p.read_bytes()
    finally:
        subprocess.run(["chmod", "-N", str(p)], check=True)
    assert out == McpWriteOutcome("refused", "mcp.json is read-only; edit it by hand")
    assert after == DEFAULT.encode()
    assert _no_temp_left(tmp_path)
