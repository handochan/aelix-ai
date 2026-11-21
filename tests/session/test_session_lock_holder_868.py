"""The holder proof is an observation of an excluded second writer."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aelix_agent_core.session import _lock_proof as proof
from aelix_agent_core.session.session_lock import SessionWriterLock, session_lock_holder

from tests.env_sandbox import child_env

_STORE_OVERRIDE = """
from pathlib import Path
from aelix_agent_core.session import _lock_proof as proof
if sys.platform == "win32":
    proof._store._cache = lambda _token: Path(sys.argv[2])
else:
    proof._store._cache_base = lambda: Path(sys.argv[2])
"""

_HOLD_LOCK = """
from aelix_agent_core.session.session_lock import SessionWriterLock
lock = SessionWriterLock(sys.argv[1], writer_kind=sys.argv[3], timeout=0)
if not lock.try_acquire() or not lock.held:
    raise SystemExit("kernel lock unavailable")
with open(sys.argv[4], "w", encoding="utf-8") as ready:
    ready.write("ready")
sys.stdin.buffer.read(1)
lock.release()
"""
_HOLDER = "import sys\n" + _STORE_OVERRIDE + _HOLD_LOCK


@pytest.fixture
def proof_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = (tmp_path / "cache").resolve()
    cache.mkdir()
    if sys.platform == "win32":
        monkeypatch.setattr(proof._store, "_cache", lambda _token: cache)
    else:
        monkeypatch.setattr(proof._store, "_cache_base", lambda: cache)
    return cache


@contextlib.contextmanager
def _process_holder(path: Path, store: Path, origin: str, *, setup: str = _STORE_OVERRIDE):
    ready = store / "ready"
    ready.unlink(missing_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys\n" + setup + _HOLD_LOCK,
         str(path), str(store), origin, str(ready)],
        cwd=store,
        env=child_env(store),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file(), "independent writer did not reach acquisition"
        yield process
    finally:
        if process.poll() is None:
            try:
                process.communicate(input=b"x", timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        assert process.poll() is not None
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _denied(path: Path) -> str:
    contender = SessionWriterLock(str(path), timeout=0)
    assert contender.try_acquire() is False
    assert contender.error is None and not contender.held and not contender.degraded
    return session_lock_holder(contender.lock_path)


@pytest.fixture
def windows_store(monkeypatch: pytest.MonkeyPatch):
    if sys.platform == "win32":
        return proof._store
    # Load the real Windows Python code in isolation. Only the DLL bindings
    # and the operations at the OS boundary are replaced in the tests below.
    path = Path(proof.__file__).with_name("_lock_proof_windows.py")
    spec = importlib.util.spec_from_file_location("test_windows_lock_proof", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        # The module defines its own _bind. Give it callable DLL stubs.
        patch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: _StubDLL(), raising=False)
        spec.loader.exec_module(module)
    return module


class _StubDLL:
    def __getattr__(self, name: str):
        return lambda *args: None


def test_windows_directory_walk_requests_delete_exclusion(windows_store, monkeypatch) -> None:
    opened, closed = [], []
    directory = PureWindowsPath("C:/User/Cache")

    @contextlib.contextmanager
    def open_directory(path, *, access, share):
        opened.append((path, access, share))
        try:
            yield path
        finally:
            closed.append(path)

    monkeypatch.setattr(windows_store, "Path", PureWindowsPath)
    monkeypatch.setattr(windows_store, "_drive_type", lambda _: 3)
    monkeypatch.setattr(windows_store, "_open", open_directory)
    for name in ("_object", "_check_path", "_checked_volume", "_check_security"):
        monkeypatch.setattr(windows_store, name, lambda *args, **kwargs: None)

    with windows_store._directories(directory, "user"):
        assert [path for path, _, _ in opened] == [*reversed(directory.parents), directory]
        assert not closed
        for _, access, share in opened:
            assert access & 0x20, "FILE_TRAVERSE must participate in Windows sharing checks"
            assert share == 3, "a pin must share read/write and deny delete"
    assert closed == list(reversed([path for path, _, _ in opened]))


@pytest.mark.parametrize("redirected", [False, True])
def test_windows_cache_compares_current_and_default_known_folder(
    windows_store, monkeypatch, tmp_path: Path, redirected: bool,
) -> None:
    default = tmp_path / "default"
    current = tmp_path / "redirected" if redirected else default
    calls = []
    token = object()

    def folder(received_token, flags):
        assert received_token is token
        calls.append(flags)
        return default if flags == 0x400 else current  # KF_FLAG_DEFAULT_PATH

    monkeypatch.setattr(windows_store, "_folder", folder)
    if redirected:
        with pytest.raises(OSError, match="redirected LocalAppData"):
            windows_store._cache(token)
    else:
        assert windows_store._cache(token) == default
    assert calls == [0, 0x400]


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows directory sharing")
def test_windows_directory_pin_denies_delete_until_released(tmp_path: Path, windows_store) -> None:
    directory = tmp_path.resolve() / "pinned"
    directory.mkdir()
    moved = directory.with_name("renamed")
    probe = """
import sys
from pathlib import Path
from aelix_agent_core.session import _lock_proof_windows as store
directory, moved = map(Path, sys.argv[1:3])
if sys.argv[3] == "pinned":
    try:
        with store._open(directory, access=0x10000):  # DELETE
            raise AssertionError("opened the pinned directory for DELETE")
    except OSError as exc:
        assert exc.winerror == 32, repr(exc)  # ERROR_SHARING_VIOLATION
    try:
        directory.rename(moved)
    except PermissionError:
        pass
    else:
        raise AssertionError("renamed the pinned directory")
else:
    with store._open(directory, access=0x10000):
        pass
    directory.rename(moved)
"""

    def try_move(state: str) -> None:
        result = subprocess.run(
            [sys.executable, "-c", probe, str(directory), str(moved), state],
            cwd=tmp_path, env=child_env(tmp_path), capture_output=True, timeout=10, check=False,
        )
        assert result.returncode == 0, result.stderr

    with windows_store._user_token() as token:
        user = windows_store._token_sid(token)
    with windows_store._directories(directory, user):
        try_move("pinned")
        assert directory.is_dir() and not moved.exists()
    try_move("released")
    assert moved.is_dir() and not directory.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows LocalAppData known folder")
def test_windows_default_local_app_data_proves_independent_writer(
    tmp_path: Path, windows_store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    path = root / "transcript.jsonl"
    # Only the directory name is test-specific. Both processes use the shell
    # lookup and real ACLs, including the default profile's ancestor chain.
    namespace = f"aelix-session-lock-test-{uuid4().hex}"
    monkeypatch.setattr(windows_store, "_STORE", namespace)
    with windows_store._user_token() as token:
        default = windows_store._folder(token, 0x400) / namespace
    setup = (
        "from aelix_agent_core.session import _lock_proof_windows as store\n"
        f"store._STORE = {namespace!r}\n"
    )
    locator = default / proof._key(SessionWriterLock(str(path)).lock_path).hex()
    try:
        with _process_holder(path, root, "studio", setup=setup):
            assert locator.is_file(), "the writer did not publish under the default LocalAppData"
            assert _denied(path) == "studio"
        assert session_lock_holder(SessionWriterLock(str(path)).lock_path) == "unknown"
    finally:
        locator.unlink(missing_ok=True)
        if default.is_dir():
            default.rmdir()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows locator storage")
def test_windows_redirected_local_app_data_refuses_attribution(
    tmp_path: Path, windows_store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    redirected = root / "redirected"
    redirected.mkdir()
    path = root / "transcript.jsonl"
    # Model the shell's redirected answer without changing the user's profile.
    # The real default query and all downstream storage checks remain active.
    setup = """
from pathlib import Path
from aelix_agent_core.session import _lock_proof_windows as store
folder = store._folder
store._folder = lambda token, flags: (
    folder(token, flags) if flags & 0x400 else Path(sys.argv[2]) / "redirected"
)
"""
    folder = windows_store._folder
    monkeypatch.setattr(
        windows_store, "_folder",
        lambda token, flags: folder(token, flags) if flags & 0x400 else redirected,
    )
    with windows_store._user_token() as token, pytest.raises(OSError, match="redirected LocalAppData"):
        windows_store._cache(token)
    with _process_holder(path, root, "studio", setup=setup):
        assert _denied(path) == "unknown"
        assert not (redirected / windows_store._STORE).exists()


@pytest.mark.parametrize("error", ["EAGAIN", "ECONNRESET"])
def test_responder_retries_transient_accept_failure(proof_root: Path, error: str) -> None:
    fault = proof_root / "accept-failed"
    setup = _STORE_OVERRIDE + f"""
import errno
import socket
fault = Path(sys.argv[2]) / "accept-failed"
accept = socket.socket.accept

def fail_once(sock):
    if sock is proof._listener and not fault.exists():
        fault.touch()
        raise OSError(errno.{error}, "injected transient accept failure")
    return accept(sock)

socket.socket.accept = fail_once
"""
    path = proof_root / "transcript.jsonl"
    with _process_holder(path, proof_root, "studio", setup=setup):
        holder = _denied(path)
        assert fault.is_file(), "the real challenge must reach the injected accept failure"
        assert holder == "studio"
        assert _denied(path) == "studio"


def _check_accept_failure(root: Path, error: str, later_holder: str, backoff: bool) -> None:
    path = root / "original.jsonl"
    writer = SessionWriterLock(str(path), timeout=0, writer_kind="studio")
    accept = socket.socket.accept
    injected = threading.Event()
    delays: list[float] = []

    def fail_once(listener: socket.socket):
        if listener is proof._listener and not injected.is_set():
            injected.set()
            code = getattr(errno, error)
            if error.startswith("WSA"):
                raise OSError(0, "injected accept failure", None, code)
            raise OSError(code, "injected accept failure")
        return accept(listener)

    def record_backoff(delay: float) -> None:
        delays.append(delay)
        time.sleep(delay)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(proof, "time", SimpleNamespace(monotonic=time.monotonic, sleep=record_backoff))
        assert writer.try_acquire() and writer.held
        try:
            assert _denied(path) == "studio"
            with pytest.MonkeyPatch.context() as accept_patch:
                accept_patch.setattr(socket.socket, "accept", fail_once)
                _denied(path)
                assert injected.is_set(), "the real challenge must reach the injected accept failure"
        finally:
            writer.release()

        # A different label on a different session rules out stale attribution.
        path = root / "later.jsonl"
        writer = SessionWriterLock(str(path), timeout=0, writer_kind="cli")
        assert writer.try_acquire() and writer.held
        try:
            observed = _denied(path)
            assert observed == later_holder, f"{error}: expected {later_holder}, got {observed}"
            assert bool(delays) == backoff
            assert all(delay > 0 for delay in delays)
        finally:
            writer.release()


@pytest.mark.parametrize(
    ("error", "later_holder", "backoff"),
    [
        ("EPROTO", "cli", False),
        ("EINTR", "cli", False),
        ("ECONNABORTED", "cli", False),
        ("EMFILE", "cli", True),
        ("ENFILE", "cli", True),
        ("EBADF", "unknown", False),
        *(
            [("WSAEMFILE", "cli", True), ("WSAENOBUFS", "cli", True)]
            if sys.platform == "win32"
            else []
        ),
    ],
)
def test_accept_failure_attribution_on_later_session(
    proof_root: Path, error: str, later_holder: str, backoff: bool
) -> None:
    # Give the responder its own process so a fatal error cannot affect other tests.
    script = "import sys\n" + _STORE_OVERRIDE + """
from tests.session.test_session_lock_holder_868 import _check_accept_failure
_check_accept_failure(Path(sys.argv[2]), sys.argv[1], sys.argv[3], sys.argv[4] == "True")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, error, str(proof_root), later_holder, str(backoff)],
        cwd=Path(__file__).resolve().parents[2],
        env=child_env(proof_root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("writer", ["studio", "cli", "unknown"])
def test_independent_writer_label_and_release(proof_root: Path, writer: str) -> None:
    path = proof_root / "transcript.jsonl"
    path.write_bytes(b"history")
    with _process_holder(path, proof_root, writer):
        assert _denied(path) == writer
        assert path.read_bytes() == b"history"
    lock = SessionWriterLock(str(path), timeout=0)
    assert session_lock_holder(lock.lock_path) == "unknown"
    assert lock.try_acquire() and lock.held
    lock.release()


def test_crash_stale_locator_and_unmarked_takeover(proof_root: Path) -> None:
    path = proof_root / "transcript.jsonl"
    with _process_holder(path, proof_root, "studio") as process:
        assert _denied(path) == "studio"
        process.kill()
        process.wait(timeout=10)
    with _process_holder(path, proof_root, "unknown"):
        assert _denied(path) == "unknown"
    with _process_holder(path, proof_root, "cli"):
        assert _denied(path) == "cli"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permits renaming an open lock directory")
@pytest.mark.parametrize("successor", ["unknown", "cli"])
def test_directory_replacement_and_retiring_writer_preserve_successor(
    proof_root: Path, successor: str
) -> None:
    directory = proof_root / "sessions"
    directory.mkdir()
    path = directory / "transcript.jsonl"
    with _process_holder(path, proof_root, "studio") as old:
        assert _denied(path) == "studio"
        directory.rename(proof_root / "moved")
        directory.mkdir()
        with _process_holder(path, proof_root, successor):
            assert _denied(path) == successor
            old.communicate(input=b"x", timeout=10)
            assert _denied(path) == successor


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX modes and FIFO semantics")
@pytest.mark.parametrize(
    "damage", ["absent", "forged", "symlink", "fifo", "public", "hardlink", "oversize"]
)
def test_invalid_locator_cannot_attribute_or_admit(proof_root: Path, damage: str) -> None:
    path = proof_root / "transcript.jsonl"
    with _process_holder(path, proof_root, "studio"):
        assert _denied(path) == "studio"
        (owner_file,) = (proof_root / "aelix-session-locks-v2").iterdir()
        record = owner_file.read_bytes()
        owner_file.unlink()
        if damage == "forged":
            parts = record.split()
            owner_file.write_bytes(b" ".join([*parts[:2], b"e" * 64]) + b"\n")
            owner_file.chmod(0o600)
        elif damage == "symlink":
            owner_file.symlink_to(path)
        elif damage == "fifo":
            os.mkfifo(owner_file, 0o600)
        elif damage in {"public", "hardlink", "oversize"}:
            owner_file.write_bytes(record + (b"x" * 200 if damage == "oversize" else b""))
            owner_file.chmod(0o644 if damage == "public" else 0o600)
            if damage == "hardlink":
                os.link(owner_file, proof_root / "link")
        assert _denied(path) == "unknown"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permits lock-directory replacement after signing"
)
def test_post_response_rewalk_rejects_recreated_path(
    proof_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = proof_root / "sessions"
    directory.mkdir()
    path = directory / "transcript.jsonl"
    writer = SessionWriterLock(str(path), writer_kind="studio", timeout=0)
    challenge = proof._challenge
    replacement = SessionWriterLock(str(path), timeout=0)

    def replace_after_reply(*args):
        reply = challenge(*args)
        directory.rename(proof_root / "moved")
        directory.mkdir()
        assert replacement.try_acquire()
        return reply

    assert writer.try_acquire()
    monkeypatch.setattr(proof, "_challenge", replace_after_reply)
    try:
        assert _denied(path) == "unknown"
        assert replacement.held, (
            "the signed proof was delivered before a different file took its place"
        )
    finally:
        replacement.release()
        writer.release()


def test_failed_publication_revokes_registered_epoch(
    proof_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = proof_root / "transcript.jsonl"
    writer = SessionWriterLock(str(path), writer_kind="studio", timeout=0)
    real_publish = proof._store.publish_locator

    def publish_then_raise(*args):
        real_publish(*args)
        raise OSError("after atomic replacement")

    monkeypatch.setattr(proof._store, "publish_locator", publish_then_raise)
    try:
        assert writer.try_acquire() and writer.held and writer.error is None
        assert _denied(path) == "unknown"
        assert list((proof_root / "aelix-session-locks-v2").iterdir())
        assert proof._key(writer.lock_path) not in proof._registry
    finally:
        writer.release()


def test_revoke_precedes_unlock_and_reacquire_is_fresh(
    proof_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = proof_root / "transcript.jsonl"
    writer = SessionWriterLock(str(path), writer_kind="studio", timeout=0)
    assert writer.try_acquire()
    epoch = writer._acquisition
    old_retire = proof.retire
    observed = []

    def check_retirement(acquisition):
        old_retire(acquisition)
        if acquisition is epoch:
            observed.append(_denied(path))

    monkeypatch.setattr(proof, "retire", check_retirement)
    try:
        assert writer.try_acquire() and writer._acquisition is epoch
        assert _denied(path) == "studio"
        writer.release()
        assert observed == ["unknown"]
        assert writer.try_acquire() and writer._acquisition is not epoch
        assert _denied(path) == "studio"
    finally:
        writer.release()


def test_acquisition_keeps_kernel_lock_through_publication_and_concurrent_release(
    proof_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = SessionWriterLock(str(proof_root / "transcript.jsonl"), writer_kind="cli", timeout=0)
    real_publish = proof._store.publish_locator
    entered, proceed = threading.Event(), threading.Event()
    results = []

    def blocked_publish(*args):
        entered.set()
        assert proceed.wait(5)
        real_publish(*args)

    monkeypatch.setattr(proof._store, "publish_locator", blocked_publish)
    acquire = threading.Thread(target=lambda: results.append(writer.try_acquire()))
    release = threading.Thread(target=writer.release)
    acquire.start()
    try:
        assert entered.wait(5)
        assert _denied(Path(writer.session_path)) == "unknown"
        release.start()
    finally:
        proceed.set()
        acquire.join(5)
        if release.ident is not None:
            release.join(5)
    assert results == [True] and not writer.held
    assert session_lock_holder(writer.lock_path) == "unknown"


def test_responder_rejects_a_challenge_for_a_different_file(proof_root: Path) -> None:
    writer = SessionWriterLock(str(proof_root / "transcript.jsonl"), writer_kind="studio", timeout=0)
    assert writer.try_acquire()
    try:
        acquisition = writer._acquisition
        assert acquisition is not None
        valid = proof._DOMAIN + acquisition.key + b"n" * 16 + acquisition.identity
        assert proof._sign(valid) is not None
        assert proof._sign(valid[:-1] + bytes([valid[-1] ^ 1])) is None
        assert proof._sign(
            proof._DOMAIN + b"x" * 32 + b"n" * 16 + acquisition.identity
        ) is None
    finally:
        writer.release()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX directory modes")
def test_untrusted_session_parent_does_not_publish(proof_root: Path) -> None:
    parent = proof_root / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    path = parent / "transcript.jsonl"
    with _process_holder(path, proof_root, "studio"):
        assert _denied(path) == "unknown"
        assert not (proof_root / "aelix-session-locks-v2").exists()


def test_failed_retirement_still_invalidates_the_old_holder(
    proof_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = proof_root / "transcript.jsonl"
    writer = SessionWriterLock(str(path), writer_kind="studio", timeout=0)
    assert writer.try_acquire()
    assert _denied(path) == "studio"
    real_retire = proof.retire

    def unavailable(acquisition):
        if acquisition is not None:
            raise OSError("injected retirement failure")
        real_retire(acquisition)

    with monkeypatch.context() as patch:
        patch.setattr(proof, "retire", unavailable)
        with pytest.raises(OSError, match="injected retirement failure"):
            writer.release()
    assert not writer.held
    with _process_holder(path, proof_root, "unknown"):
        assert _denied(path) == "unknown"
    real_retire(proof._registry.get(proof._key(writer.lock_path)))


@pytest.mark.skipif(sys.platform == "win32", reason="requires the POSIX Python fork audit event")
def test_same_thread_fork_cannot_wait_on_its_own_transition() -> None:
    program = """
import os
from aelix_agent_core.session._lock_fork import transition
with transition():
    try:
        os.fork()
    except RuntimeError as exc:
        assert 'during a session-lock transition' in str(exc)
    else:
        raise AssertionError('fork passed inside a protected transition')
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX select has a numeric descriptor limit")
def test_high_descriptor_listener_can_prove_its_held_session(proof_root: Path) -> None:
    import resource

    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if 0 <= soft <= 1050:
        pytest.skip("the current process descriptor limit cannot reach the regression boundary")
    path = proof_root / "transcript.jsonl"
    program = _HOLDER.replace(
        "lock = SessionWriterLock",
        "import os\nfds = [os.open(os.devnull, os.O_RDONLY) for _ in range(1030)]\n"
        "lock = SessionWriterLock",
        1,
    )
    ready = proof_root / "ready-high-fd"
    with subprocess.Popen(
        [sys.executable, "-c", program, str(path), str(proof_root), "studio", str(ready)],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            import time

            deadline = time.monotonic() + 10
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            assert _denied(path) == "studio"
        finally:
            try:
                process.communicate(input=b"x", timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
