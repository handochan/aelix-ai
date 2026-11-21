"""Best-effort attribution for an acquisition of the authoritative writer lock."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import os
import re
import secrets
import select
import socket
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol
from weakref import ReferenceType

from . import _lock_fork

if sys.platform == "win32":
    try:
        from . import _lock_proof_windows as _store
    except (AttributeError, ImportError, OSError):
        _store = None
else:
    from . import _lock_proof_posix as _store

WriterKind = Literal["studio", "cli", "unknown"]

_DOMAIN = b"aelix-lock-proof-v2\0"
_REQUEST_SIZE = len(_DOMAIN) + 32 + 16 + 25
_RESPONSE_SIZE = 33
_LOOPBACK = "127.0.0.1"
_RECORD = re.compile(rb"2 ([1-9][0-9]{0,4}) ([0-9a-f]{64})\n")
_SOCKET_BUDGET = 0.075

# Readiness can race with an aborted client on a nonblocking listener.
_ACCEPT_RETRY = {
    errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR, errno.ECONNABORTED,
    errno.ECONNRESET, errno.EPROTO,
}
_ACCEPT_BACKOFF = {errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM}
_ACCEPT_BACKOFF_SECONDS = 0.05
if sys.platform == "linux":
    # Linux accept(2) can report pending errors from the new TCP connection.
    _ACCEPT_RETRY.update({
        errno.ENETDOWN, errno.ENOPROTOOPT, errno.EHOSTDOWN, errno.ENONET,
        errno.EHOSTUNREACH, errno.EOPNOTSUPP, errno.ENETUNREACH, errno.EPERM,
        errno.ESOCKTNOSUPPORT, errno.EPROTONOSUPPORT, errno.ETIMEDOUT,
    })
    _ACCEPT_BACKOFF.add(errno.ENOSR)
elif sys.platform == "win32":
    _ACCEPT_RETRY.update({errno.WSAEWOULDBLOCK, errno.WSAEINTR, errno.WSAECONNRESET})
    _ACCEPT_BACKOFF.update({
        errno.WSAEMFILE, errno.WSAENOBUFS, errno.WSAEINPROGRESS, errno.WSAENETDOWN,
    })


class ProofOwner(Protocol):
    def _owns_proof(self, acquisition: Acquisition) -> bool: ...


@dataclass(frozen=True, eq=False)
class Acquisition:
    key: bytes
    path: str
    identity: bytes
    secret: bytes
    kind: bytes
    owner: ReferenceType[ProofOwner]


_registry: dict[bytes, Acquisition] = {}
_membership = threading.Lock()
_start_listener = threading.Lock()
_listener: socket.socket | None = None


def _fresh_process() -> None:
    global _listener, _membership, _start_listener
    _registry.clear()
    _listener = None
    _membership = threading.Lock()
    _start_listener = threading.Lock()


_lock_fork.set_child_reset(_fresh_process)


def _key(path: str) -> bytes:
    return hashlib.sha256(os.fsencode(path)).digest()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("lock proof deadline expired")
    return remaining


def _receive(sock: socket.socket, size: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < size:
        sock.settimeout(_remaining(deadline))
        packet = sock.recv(size - len(result))
        if not packet:
            raise OSError("truncated lock proof")
        result.extend(packet)
    return bytes(result)


def _sign(request: bytes) -> bytes | None:
    if _store is None or not request.startswith(_DOMAIN):
        return None
    key = request[len(_DOMAIN) : len(_DOMAIN) + 32]
    with _membership:
        acquisition = _registry.get(key)
    if acquisition is None or request[-25:] != acquisition.identity:
        return None
    with _store.named_identity(acquisition.path) as named, _membership:
        owner = acquisition.owner()
        if (
            owner is not None
            and owner._owns_proof(acquisition)
            and _registry.get(key) is acquisition
            and named == acquisition.identity
        ):
            kind = acquisition.kind
            return kind + hmac.digest(acquisition.secret, request + kind, "sha256")
    return None


def _wait_for_connection(listener: socket.socket) -> None:
    if sys.platform == "win32":
        select.select([listener], [], [])
        return
    # A long-running POSIX worker can allocate the listener above FD_SETSIZE.
    # poll needs no additional OS descriptor that a forked child could inherit.
    poller = select.poll()
    poller.register(listener, select.POLLIN)
    poller.poll()


def _serve(listener: socket.socket) -> None:
    while True:
        try:
            _wait_for_connection(listener)
            with _lock_fork.transition():
                connection, _ = listener.accept()
                _lock_fork.track_socket(connection)
        except OSError as exc:
            # Winsock preserves the socket-specific code in winerror.
            code = getattr(exc, "winerror", None) or exc.errno
            if code in _ACCEPT_BACKOFF:
                # A pending client can keep the listener readable during exhaustion.
                time.sleep(_ACCEPT_BACKOFF_SECONDS)
            elif code not in _ACCEPT_RETRY:
                return
            continue
        try:
            deadline = time.monotonic() + _SOCKET_BUDGET
            request = _receive(connection, _REQUEST_SIZE, deadline)
            _expect_eof(connection, deadline)
            reply = _sign(request)
            if reply is not None:
                connection.settimeout(_remaining(deadline))
                connection.sendall(reply)
        except Exception:  # noqa: BLE001 - proof is optional and handles untrusted local input
            pass
        finally:
            _lock_fork.close_socket(connection)


def _port() -> int:
    global _listener
    # Startup I/O is independent of the short registration/signing mutex.
    with _start_listener:
        if _listener is None:
            listener = _lock_fork.track_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            try:
                listener.bind((_LOOPBACK, 0))
                listener.listen(4)
                listener.setblocking(False)
                threading.Thread(target=_serve, args=(listener,), daemon=True).start()
            except BaseException:
                _lock_fork.close_socket(listener)
                raise
            _listener = listener
        return _listener.getsockname()[1]


def prepare(
    path: str, fd: int, kind: WriterKind, owner: ReferenceType[ProofOwner]
) -> Acquisition | None:
    if _store is None or kind == "unknown":
        return None
    try:
        identity = _store.held_identity(fd)
        return Acquisition(
            _key(path), path, identity, secrets.token_bytes(32),
            b"S" if kind == "studio" else b"C", owner,
        )
    except Exception:  # noqa: BLE001 - attribution is optional
        return None


def publish(acquisition: Acquisition | None) -> None:
    if _store is None or acquisition is None:
        return
    published = False
    try:
        with _store.named_identity(acquisition.path) as named:
            if acquisition.identity != named:
                return
            port = _port()
            with _membership:
                _registry[acquisition.key] = acquisition
            record = f"2 {port} {acquisition.secret.hex()}\n".encode("ascii")
            _store.publish_locator(acquisition.key.hex(), f".{secrets.token_hex(16)}", record)
        published = True
    except Exception:  # noqa: BLE001 - preserve the successfully acquired writer lock
        pass
    finally:
        if not published:
            retire(acquisition)


def retire(acquisition: Acquisition | None) -> None:
    if acquisition is None:
        return
    with _membership:
        if _registry.get(acquisition.key) is acquisition:
            del _registry[acquisition.key]
    # No conditional unlink is available. A successor can replace this path
    # even before our unlock if the session directory moved. The stale
    # locator cannot pass a fresh challenge and the next writer replaces it.


@contextlib.contextmanager
def _client() -> Iterator[socket.socket]:
    with _lock_fork.transition():
        sock = _lock_fork.track_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    try:
        yield sock
    finally:
        _lock_fork.close_socket(sock)


def _expect_eof(sock: socket.socket, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))
    if sock.recv(1):
        raise OSError("oversize lock proof")


def _challenge(port: int, request: bytes, deadline: float) -> bytes:
    with _client() as sock:
        sock.settimeout(_remaining(deadline))
        sock.connect((_LOOPBACK, port))
        sock.settimeout(_remaining(deadline))
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        reply = _receive(sock, _RESPONSE_SIZE, deadline)
        _expect_eof(sock, deadline)
        return reply


def _verify(path: str) -> WriterKind:
    if _store is None:
        return "unknown"
    key = _key(path)
    with _store.named_identity(path) as identity, _store.read_locator(key.hex()) as original:
        _, record = original
        match = _RECORD.fullmatch(record)
        if match is None or len(record) > 160:
            return "unknown"
        port = int(match[1])
        if port > 65535:
            return "unknown"
        secret = bytes.fromhex(match[2].decode("ascii"))
        request = _DOMAIN + key + secrets.token_bytes(16) + identity
        deadline = time.monotonic() + _SOCKET_BUDGET
        reply = _challenge(port, request, deadline)
        kind, mac = reply[:1], reply[1:]
        if kind not in (b"S", b"C") or not hmac.compare_digest(
            mac, hmac.digest(secret, request + kind, "sha256")
        ):
            return "unknown"
        with _store.named_identity(path) as current, _store.read_locator(key.hex()) as locator:
            _remaining(deadline)
            if current != identity or locator != original:
                return "unknown"
        return "studio" if kind == b"S" else "cli"


def lookup(path: str) -> WriterKind:
    try:
        return _verify(path)
    except Exception:  # noqa: BLE001 - the kernel has already denied admission
        return "unknown"
