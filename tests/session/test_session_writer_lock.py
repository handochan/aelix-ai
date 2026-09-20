"""#137 / ADR-0244 — one live writer per session file.

The defect, restated as a test in §1: two writers on one file, all four lines
on disk, valid JSON throughout, and one whole turn invisible on reload. That
assertion is **green on the parent commit and green here** — this lane does
not change the leaf pointer, and saying otherwise in a docstring would pin the
defect as an invariant. It is here as the *statement of what ownership has to
prevent*, immediately above the test that shows it does.

Nothing in this file is skipped on any platform. #46 is the standing
precedent: both existing ``fcntl`` sites silently ``return None`` on Windows
and no test noticed. Every assertion below runs on both legs, and §4 asserts
which backend is actually in use rather than merely that nothing crashed.
"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
    ReadOnlySessionStorage,
    Session,
    SessionError,
    SessionWriterLock,
    lock_path_for,
)
from aelix_agent_core.session.context import build_session_context
from aelix_ai.messages import TextContent, UserMessage

# A child that takes the lock, says so, and then sits there — a live ``aelix``
# in another terminal, reduced to the one thing that matters here. It is killed
# rather than asked to exit, because "the kernel releases it" is the whole
# reason there is no heartbeat.
_HOLDER = (
    "import sys, time\n"
    "from aelix_agent_core.session.session_lock import SessionWriterLock\n"
    "lock = SessionWriterLock(sys.argv[1])\n"
    "print('HELD' if lock.try_acquire() else 'LOST', flush=True)\n"
    "time.sleep(120)\n"
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


async def _fresh_session(tmp_path: Path) -> tuple[JsonlSessionRepo, str]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    metadata = await session.get_metadata()
    return repo, metadata.path


# === 1. The defect this exists to make unreachable ==========================


async def test_two_writers_on_one_file_lose_a_turn(tmp_path: Path) -> None:
    """The issue's own measurement, verbatim, at the storage layer.

    THIS ASSERTION IS GREEN BEFORE THIS LANE AND AFTER IT — it is not a
    reproduction gate, and calling it one would pin the defect as an
    invariant. (The file as a whole cannot even import on the parent commit,
    because nothing it imports exists there yet; that is an ``ImportError``,
    not a behavioural red.) ``append_entry`` reparents onto a process-local
    ``_current_leaf_id`` and this lane deliberately does not touch that: a
    tail-re-read would let two writers share a file, and two writers on one
    file hand the provider a different prefix every turn, which breaks the
    prompt cache. What changes is that nothing in the product hands out the
    second writable storage — see §2 and §5, and the two-process before/after
    in the commit message.
    """

    repo, path = await _fresh_session(tmp_path)
    a = Session(await JsonlSessionStorage.open(repo._fs, path))
    b = Session(await JsonlSessionStorage.open(repo._fs, path))

    await a.append_message(_user("A-turn-1"))
    await b.append_message(_user("B-turn-1"))
    await a.append_message(_user("A-turn-2"))

    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 4
    assert all(json.loads(ln) for ln in lines), "every line on disk is valid JSON"

    reopened = await JsonlSessionStorage.open(repo._fs, path)
    branch = await reopened.get_path_to_root(await reopened.get_leaf_id())
    replayed = [
        part.text
        for message in build_session_context(branch).messages
        for part in (message.content if isinstance(message.content, list) else [])
        if isinstance(part, TextContent)
    ]
    assert replayed == ["A-turn-1", "A-turn-2"], "B's turn is on disk and invisible"


# === 2. Ownership ===========================================================


async def test_a_second_process_cannot_take_the_lock(tmp_path: Path) -> None:
    """Cross-process, because in-process is not the defect.

    This is the assertion that must hold on the Windows leg: ``LockFileEx``
    there, ``fcntl.flock`` here, one test.
    """

    _repo, path = await _fresh_session(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, path], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "HELD"
        contender = SessionWriterLock(path)
        assert contender.try_acquire() is False
        assert contender.held is False
        assert contender.degraded is False, "a held lock is not a degraded one"
    finally:
        holder.kill()
        holder.wait()


async def test_the_lock_dies_with_the_process_that_held_it(tmp_path: Path) -> None:
    """``kill -9`` releases it, with no reclamation code anywhere.

    This is the assertion D3's "no heartbeat, no lease, no stale sweep" rests
    on. If it ever fails, the missing heartbeat is a bug rather than a
    simplification.
    """

    _repo, path = await _fresh_session(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, path], stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "HELD"
    holder.kill()
    holder.wait()

    survivor = SessionWriterLock(path, timeout=5.0)
    assert survivor.try_acquire() is True
    assert survivor.held is True
    survivor.release()


async def test_release_lets_the_next_opener_in(tmp_path: Path) -> None:
    """A handoff inside one process: ``/quit`` then relaunch."""

    _repo, path = await _fresh_session(tmp_path)
    first = SessionWriterLock(path)
    assert first.try_acquire() is True
    second = SessionWriterLock(path)
    assert second.try_acquire() is False
    first.release()
    assert first.held is False
    assert second.try_acquire() is True
    second.release()


async def test_acquire_and_release_are_idempotent(tmp_path: Path) -> None:
    """Teardown runs on paths that may already have torn down."""

    _repo, path = await _fresh_session(tmp_path)
    lock = SessionWriterLock(path)
    assert lock.try_acquire() is True
    assert lock.try_acquire() is True, "re-acquiring our own file is not contention"
    lock.release()
    lock.release()
    assert lock.held is False


# === 3. The sidecar =========================================================


async def test_the_lock_is_a_sidecar_the_repo_cannot_see(tmp_path: Path) -> None:
    """``<session>.jsonl.lock`` — not the ``.jsonl``, and not a session.

    Two properties in one test because they are the same decision. Locking
    the session file itself would make a read-only viewer's ``read()`` fail on
    Windows only (``LockFileEx`` is mandatory), and a sidecar that ended in
    ``.jsonl`` would show up in the session list as a corrupt session.
    """

    repo, path = await _fresh_session(tmp_path)
    lock = SessionWriterLock(path)
    assert lock.lock_path == lock_path_for(path) == f"{path}.lock"
    assert not lock.lock_path.endswith(".jsonl")
    assert lock.try_acquire() is True
    try:
        # The session file is still readable while the lock is held — the
        # assertion that only ever fails on Windows, and only if we ever move
        # the lock onto the ``.jsonl`` itself.
        assert Path(path).read_text(encoding="utf-8").strip() != ""

        listed = [meta.path for meta in await repo.list()]
        assert listed == [path]
        most_recent = await repo.find_most_recent(str(tmp_path))
        assert most_recent is not None
        assert most_recent.path == path
    finally:
        lock.release()


# === 4. Which backend is actually running ===================================


def test_the_platform_backend_is_the_native_one(tmp_path: Path) -> None:
    """#46's missing assertion: name the class, do not just avoid a crash.

    A Windows leg whose locking silently no-op'd would still pass every
    behavioural test above if the no-op were symmetric. This one would not.
    """

    from filelock import SoftFileLock

    lock = SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is True
    try:
        assert lock.degraded is False, (
            "a normal temp directory supports file locking; a soft fallback "
            "here means the native backend is not being used at all"
        )
        inner = lock._lock
        assert inner is not None
        assert not isinstance(inner, SoftFileLock)
        expected = "WindowsFileLock" if sys.platform == "win32" else "UnixFileLock"
        assert type(inner).__name__ == expected
    finally:
        lock.release()


def test_a_filesystem_without_locking_degrades_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFS without lockd, some FUSE mounts: ENOSYS from ``flock``.

    Degraded is not held. The caller is told, and it carries on rather than
    locking the user out of their own sessions over a mount option — and it
    does **not** silently keep a soft marker, which is the stale-lock problem
    D3 refuses to own.
    """

    import aelix_agent_core.session.session_lock as session_lock

    class _EnosysLock:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise OSError(errno.ENOSYS, "flock is not implemented here")

    monkeypatch.setattr(session_lock, "FileLock", _EnosysLock)
    lock = session_lock.SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is True
    assert lock.degraded is True
    assert lock.held is False
    lock.release()


def test_a_soft_fallback_is_dropped_rather_than_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OTHER door to the same verdict, and the reason we pass no keyword.

    filelock's default is to swap ``self.__class__`` to ``SoftFileLock`` when
    the filesystem answers ``ENOSYS``, and a soft lock is an
    ``O_CREAT|O_EXCL`` marker that outlives the process that made it. The
    keyword that turns that off (``fallback_to_soft=``) only exists from
    3.32.3, so instead we ask what the lock BECAME. A soft lock that was
    actually taken is released again: leaving it would hand the user a session
    nobody can open until they delete a file by hand.
    """

    import aelix_agent_core.session.session_lock as session_lock
    from filelock import SoftFileLock

    released: list[str] = []

    class _DegradedToSoft(SoftFileLock):
        def acquire(self, *_args: object, **_kwargs: object) -> None:
            return None

        def release(self, *_args: object, **_kwargs: object) -> None:
            released.append("yes")

    monkeypatch.setattr(session_lock, "FileLock", _DegradedToSoft)
    lock = session_lock.SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is True
    assert lock.degraded is True
    assert lock.held is False
    # ``in`` rather than ``==``: filelock's ``BaseFileLock.__del__`` releases
    # again when the dropped object is collected, so the count is not ours to
    # pin — that we released it at all is.
    assert "yes" in released, "a stale-able marker is not left behind"


# === 5. The belt: read-only storage =========================================


async def test_read_only_storage_refuses_the_two_writes(tmp_path: Path) -> None:
    """``append_entry`` and ``set_leaf_id`` are the Protocol's only writes."""

    repo, path = await _fresh_session(tmp_path)
    inner = await JsonlSessionStorage.open(repo._fs, path)
    await Session(inner).append_message(_user("owner's turn"))
    viewer = ReadOnlySessionStorage(inner)

    entry = (await inner.get_entries())[-1]
    with pytest.raises(SessionError) as append_exc:
        await viewer.append_entry(entry)
    assert append_exc.value.code == "read_only"
    with pytest.raises(SessionError) as leaf_exc:
        await viewer.set_leaf_id(None)
    assert leaf_exc.value.code == "read_only"


async def test_read_only_storage_answers_every_read(tmp_path: Path) -> None:
    """A viewer that cannot read is not a viewer."""

    repo, path = await _fresh_session(tmp_path)
    inner = await JsonlSessionStorage.open(repo._fs, path)
    await Session(inner).append_message(_user("owner's turn"))
    viewer = ReadOnlySessionStorage(inner)

    assert await viewer.get_metadata() == await inner.get_metadata()
    assert await viewer.get_leaf_id() == await inner.get_leaf_id()
    assert await viewer.get_entries() == await inner.get_entries()
    entry = (await inner.get_entries())[-1]
    assert await viewer.get_entry(entry.id) == entry
    assert await viewer.find_entries("message") == await inner.find_entries("message")
    assert await viewer.get_label(entry.id) == await inner.get_label(entry.id)
    assert await viewer.get_path_to_root(entry.id) == await inner.get_path_to_root(entry.id)
    # Pure over the id set; refusing it would only move the error somewhere
    # less informative.
    assert isinstance(await viewer.create_entry_id(), str)


async def test_read_only_storage_keeps_session_file_and_cwd(tmp_path: Path) -> None:
    """The two synchronous private reaches the Protocol does not cover.

    ``Session.session_file`` reads ``storage._metadata`` directly, and
    ``AgentHarness.session_path`` reads ``storage._file_path``. A wrapper that
    implemented only the ten Protocol methods would make both ``None`` — which
    silently drops the previous path from the ``session_start`` /
    ``session_shutdown`` payloads and leaves a delegated child's
    ``parent_path`` unset, so no child record is written at all.
    """

    repo, path = await _fresh_session(tmp_path)
    inner = await JsonlSessionStorage.open(repo._fs, path)
    viewer = Session(ReadOnlySessionStorage(inner))
    assert viewer.session_file == path
    assert (await viewer.get_metadata()).cwd == str(tmp_path)


async def test_a_read_only_session_refuses_an_append_through_session(
    tmp_path: Path,
) -> None:
    """End of the belt: the path a turn actually takes."""

    repo, path = await _fresh_session(tmp_path)
    inner = await JsonlSessionStorage.open(repo._fs, path)
    viewer = Session(ReadOnlySessionStorage(inner))
    before = len(await viewer.get_entries())
    with pytest.raises(SessionError) as exc:
        await viewer.append_message(_user("a viewer's turn"))
    assert exc.value.code == "read_only"
    assert len(await viewer.get_entries()) == before, "and nothing reached the file"


# === 6. The doors review found open =========================================


def test_an_older_filelock_raises_instead_of_degrading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``NotImplementedError`` is not an ``OSError``, and it escaped.

    Every filelock the manifest still accepts below 3.25.2 — the floor is
    ``>=3.12`` — turns a ``flock`` ``ENOSYS`` into
    ``NotImplementedError("FileSystem does not appear to support flock")``
    rather than falling back to a ``SoftFileLock``. Measured against
    ``fcntl.flock`` forced to answer ``ENOSYS``: 3.12.0, 3.16.1 and 3.20.0
    raise; 3.25.2, 3.29.0 and 4.0.1 degrade.

    The two arms that ask ``isinstance(lock, SoftFileLock)`` cannot see this
    one: before it was caught, it escaped ``try_acquire``, escaped
    ``_resolve_session_ownership`` and reached ``main_sync``'s bare ``raise``,
    so a user whose environment already pinned an older filelock (torch,
    huggingface_hub, virtualenv and tox all pull one) and whose sessions root
    is on NFS-without-lockd could not start aelix **at all** — including a
    bare ``aelix`` with a brand-new session, since ownership is resolved
    unconditionally at startup.
    """

    import aelix_agent_core.session.session_lock as session_lock

    class _OldFilelock:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise NotImplementedError(
                "FileSystem does not appear to support flock; use SoftFileLock instead"
            )

    monkeypatch.setattr(session_lock, "FileLock", _OldFilelock)
    lock = session_lock.SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is True, "aelix starts"
    assert lock.degraded is True
    assert lock.held is False
    assert lock.error is None, "the filesystem is at fault, not the sidecar"
    lock.release()


@pytest.mark.parametrize(
    "code",
    [errno.ENOSYS, errno.ENOLCK, errno.EINVAL, errno.EOPNOTSUPP],
)
def test_the_errnos_that_mean_this_mount_cannot_lock_degrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """The third door to the same verdict, held open only this wide."""

    import aelix_agent_core.session.session_lock as session_lock

    class _Unlockable:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise OSError(code, "no locking here")

    monkeypatch.setattr(session_lock, "FileLock", _Unlockable)
    lock = session_lock.SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is True
    assert lock.degraded is True
    lock.release()


@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.ELOOP, errno.EROFS])
def test_an_unusable_sidecar_is_not_a_filesystem_without_locking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Fail CLOSED. The old code let all four of these through as "you own it".

    ``sudo aelix`` leaves a root-owned ``<session>.jsonl.lock``; a sessions
    directory shared between accounts does the same; filelock opens the
    sidecar with ``O_NOFOLLOW``, so a symlinked lock path is ``ELOOP``. Every
    one of those arrived as an ``OSError`` and was answered with
    ``degraded=True`` — that is, *True, you may write* — while the file's real
    owner was still appending to it. That is the one outcome this module
    exists to make impossible.
    """

    import aelix_agent_core.session.session_lock as session_lock

    class _BrokenSidecar:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise OSError(code, "the lock file cannot be used")

    monkeypatch.setattr(session_lock, "FileLock", _BrokenSidecar)
    lock = session_lock.SessionWriterLock(str(tmp_path / "s.jsonl"))
    assert lock.try_acquire() is False, "we do not own it"
    assert lock.degraded is False
    assert lock.held is False
    assert lock.error is not None and lock.error.errno == code, (
        "and the caller can say WHY rather than guessing at another terminal"
    )


def test_two_spellings_of_one_file_contend(tmp_path: Path) -> None:
    """Ownership is a property of the file, not of the string that named it.

    ``--session`` passes the user's argv straight through
    ``load_jsonl_session_metadata`` into ``meta.path``, so a symlink and its
    target reach the lock as two different strings. Keyed on the raw string,
    each got its own sidecar and **both** reported ``held=True`` — two
    terminals appending to one ``.jsonl``, which is #137 with the guard
    switched on. Hard links behave identically.
    """

    real = tmp_path / "a.jsonl"
    real.write_text("{}\n", encoding="utf-8")
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(real)

    first = SessionWriterLock(str(real))
    second = SessionWriterLock(str(alias))
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False, "the alias is the same file"
        assert second.error is None, "contention, not a broken sidecar"
        assert first.lock_path == second.lock_path
        assert first.session_path != second.session_path, (
            "each still reports the path it was asked for"
        )
    finally:
        first.release()
        second.release()
