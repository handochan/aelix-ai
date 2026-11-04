"""#680 — a failed ``/new``, ``/fork``, ``/clone`` or ``/import`` leaves no session file behind.

Each swap makes its target file before it builds the harness for it
(``repo.create``, ``repo.fork``, the import's copy, a contended ``/resume``'s
``fork_from``). When that build failed and the runtime "stayed on this session"
(``kept``) or stopped, the file stayed: the newest in the folder, so
``aelix -c`` opened it — an empty conversation, or a copy — instead of the one
the user was told they were still on. pi writes a session file only once the
conversation has something in it (``session-manager.ts`` ``_persist``), so its
``/new`` leaves no file either.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.runtime import RebuildFailedError
from aelix_agent_core.runtime import agent_session_runtime as runtime_mod
from aelix_agent_core.session import (
    JsonlSessionRepo,
    LocalFileSystem,
    SessionError,
    SessionWriterLock,
)
from aelix_agent_core.session.session_lock import lock_path_for

from tests.runtime.test_failed_rebuild_663 import _always, _once, _Rig


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.jsonl"))


async def _swap(rig: _Rig, how: str) -> None:
    runtime = rig.runtime
    if how == "new":
        await runtime.new_session()
    elif how in ("fork", "clone"):
        entry = (await rig.session.get_entries())[-1]
        await runtime.fork(entry.id, position="before" if how == "fork" else "at")
    else:
        assert rig.other.session_file is not None
        copy = rig.tmp / "import-me.jsonl"
        copy.write_text(Path(rig.other.session_file).read_text())
        await runtime.import_from_jsonl(str(copy))


@pytest.mark.parametrize("how", ["new", "fork", "clone", "import"])
@pytest.mark.parametrize("ending", ["kept", "stopped"])
async def test_a_failed_swap_leaves_no_file_of_its_own(
    tmp_path: Path, how: str, ending: str
) -> None:
    rig = await _Rig.create(tmp_path)
    here = rig.session.session_file
    # The session this stays on is the newest before the swap, by more than a
    # coarse (Windows) clock tick: ``-c`` picks by mtime, and a tie goes by name.
    assert rig.other.session_file is not None
    os.utime(rig.other.session_file, (time.time() - 60, time.time() - 60))
    root = tmp_path / "sessions"
    before = _files(root)
    boom = RuntimeError("build boom")
    rig.fail = _once(boom) if ending == "kept" else _always(boom)

    with pytest.raises(RebuildFailedError) as caught:
        await _swap(rig, how)

    assert caught.value.state == ending
    assert _files(root) == before
    # ``aelix -c`` opens the newest session in the folder: the one it stayed on.
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(root))
    recent = await repo.find_most_recent(str(tmp_path))
    assert recent is not None and recent.path == here


async def test_a_failed_import_over_an_existing_session_leaves_that_session(
    tmp_path: Path,
) -> None:
    """The import did not make the destination, so it is not the import's to remove."""

    rig = await _Rig.create(tmp_path)
    assert rig.other.session_file is not None
    source = rig.tmp / "elsewhere" / Path(rig.session.session_file or "").name
    source.parent.mkdir()
    source.write_text(Path(rig.other.session_file).read_text())
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError):
        await rig.runtime.import_from_jsonl(str(source))

    assert rig.session.session_file is not None
    assert Path(rig.session.session_file).exists()


async def test_the_file_goes_before_the_lock_does(tmp_path: Path) -> None:
    """Removed while this process still owns it, so no other terminal can have opened it."""

    rig = await _Rig.create(tmp_path)
    here = rig.session.session_file
    assert here is not None
    held = SessionWriterLock(here)
    assert held.try_acquire() is True
    rig.runtime.set_writer_lock(held)
    released: list[tuple[str, bool]] = []
    release = rig.runtime._release_unadopted

    def _watch(lock: SessionWriterLock | None) -> None:
        if lock is not None and lock is not rig.runtime.writer_lock:
            released.append((lock.session_path, Path(lock.session_path).exists()))
        release(lock)

    rig.runtime._release_unadopted = _watch  # type: ignore[method-assign]
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError):
        await rig.runtime.new_session()

    assert len(released) == 1
    [(path, existed)] = released
    assert path != here and existed is False
    # And this terminal still owns the session it stayed on.
    assert rig.runtime.writer_lock is held
    held.release()


async def test_a_contended_resume_that_forked_and_failed_leaves_no_fork(
    tmp_path: Path,
) -> None:
    rig = await _Rig.create(tmp_path)
    here = rig.session.session_file
    theirs = rig.other.session_file
    assert here is not None and theirs is not None
    mine = SessionWriterLock(here)
    assert mine.try_acquire() is True
    rig.runtime.set_writer_lock(mine)

    async def _fork(_path: str) -> str:
        return "fork"

    rig.runtime.set_on_session_contended(_fork)  # type: ignore[arg-type]
    other_terminal = SessionWriterLock(theirs)
    assert other_terminal.try_acquire() is True
    root = tmp_path / "sessions"
    before = _files(root)
    rig.fail = _once(RuntimeError("build boom"))
    try:
        with pytest.raises(RebuildFailedError):
            await rig.runtime.switch_session(theirs)
        assert _files(root) == before
        assert Path(theirs).exists()
    finally:
        other_terminal.release()
        mine.release()


async def test_a_swap_that_went_live_keeps_its_file(tmp_path: Path) -> None:
    """A raise after the new harness is installed: this process runs on that file now."""

    rig = await _Rig.create(tmp_path)
    root = tmp_path / "sessions"
    before = _files(root)

    async def _rebind(_harness: object, _reason: str) -> None:
        raise RuntimeError("rebind boom")

    rig.runtime.set_rebind_session(_rebind)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="rebind boom"):
        await rig.runtime.new_session()

    live = rig.runtime.session
    assert live is not None and live.session_file is not None
    assert live.session_file != rig.session.session_file
    assert Path(live.session_file).exists()
    assert len(_files(root)) == len(before) + 1


async def test_a_failed_import_over_another_session_leaves_that_session(
    tmp_path: Path,
) -> None:
    """Verify r1: the row above imports over the LIVE session, which the guard keeps anyway.

    Here the destination is another session in the folder, so only the import's
    own check (did the copy make the destination?) keeps it: a retried import of
    the same file over an earlier import of it must not delete that earlier one.
    """

    rig = await _Rig.create(tmp_path)
    theirs = rig.other.session_file
    assert theirs is not None
    source = rig.tmp / "elsewhere" / Path(theirs).name
    source.parent.mkdir()
    source.write_text(Path(theirs).read_text())
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.import_from_jsonl(str(source))

    assert caught.value.state == "kept"
    assert rig.runtime.session is not None
    assert rig.runtime.session.session_file != theirs
    assert Path(theirs).exists()


# === Codex review r1: the new file's own lock, and a Windows delete ===


def _own_the_session(rig: _Rig) -> SessionWriterLock:
    here = rig.session.session_file
    assert here is not None
    mine = SessionWriterLock(here)
    assert mine.try_acquire() is True
    rig.runtime.set_writer_lock(mine)
    return mine


def _break_the_new_files_lock(rig: _Rig) -> None:
    """A directory where the new file's sidecar goes: a lock nobody can take, not contention."""

    repo: Any = rig.runtime._repo
    for name in ("create", "fork", "fork_from"):
        original = getattr(repo, name)

        async def made(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            session = await _original(*args, **kwargs)
            os.mkdir(lock_path_for(session.session_file))
            return session

        setattr(repo, name, made)


@pytest.mark.parametrize("how", ["new", "fork", "clone", "contended-resume"])
async def test_a_lock_that_cannot_be_taken_leaves_no_file(tmp_path: Path, how: str) -> None:
    """It raised before the cleanup ``try``: the file stayed, the newest in the folder."""

    rig = await _Rig.create(tmp_path)
    mine = _own_the_session(rig)
    theirs = rig.other.session_file
    assert theirs is not None
    other_terminal = SessionWriterLock(theirs)
    root = tmp_path / "sessions"
    before = _files(root)
    _break_the_new_files_lock(rig)
    try:
        with pytest.raises(SessionError, match="cannot take the writer lock"):
            if how == "contended-resume":

                async def _fork(_path: str) -> str:
                    return "fork"

                rig.runtime.set_on_session_contended(_fork)  # type: ignore[arg-type]
                assert other_terminal.try_acquire() is True
                await rig.runtime.switch_session(theirs)
            else:
                await _swap(rig, how)

        assert _files(root) == before
        assert rig.runtime.session is rig.session
        assert rig.runtime.writer_lock is mine
    finally:
        other_terminal.release()
        mine.release()


async def test_a_new_file_another_terminal_opened_first_stays(tmp_path: Path) -> None:
    """Contention is not a broken lock: that file is the other terminal's now, and stays."""

    rig = await _Rig.create(tmp_path)
    mine = _own_the_session(rig)
    stolen: list[SessionWriterLock] = []
    repo: Any = rig.runtime._repo
    original = repo.create

    async def _create_then_steal(options: Any) -> Any:
        created = await original(options)
        thief = SessionWriterLock(created.session_file)
        assert thief.try_acquire() is True
        stolen.append(thief)
        return created

    repo.create = _create_then_steal
    try:
        with pytest.raises(SessionError, match="already open in another terminal"):
            await rig.runtime.new_session()
        assert len(stolen) == 1 and Path(stolen[0].session_path).exists()
    finally:
        for lock in stolen:
            lock.release()
        mine.release()


def _held_for(rig: _Rig, refusals: int, calls: list[str]) -> None:
    """``remove`` refused with ``PermissionError`` ``refusals`` times, as Windows does while a scanner holds the file."""

    remove = rig.runtime._fs.remove

    async def held(path: str, **kwargs: Any) -> None:
        calls.append(path)
        if len(calls) <= refusals:
            raise PermissionError(13, "held by another process (injected)", path)
        await remove(path, **kwargs)

    rig.runtime._fs.remove = held  # type: ignore[method-assign]


async def test_a_windows_delete_refused_for_a_moment_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    monkeypatch.setattr(runtime_mod, "sys", types.SimpleNamespace(platform="win32"))
    calls: list[str] = []
    _held_for(rig, 2, calls)
    root = tmp_path / "sessions"
    before = _files(root)
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError):
        await rig.runtime.new_session()

    assert len(calls) == 3 and len(set(calls)) == 1
    assert _files(root) == before


async def test_a_windows_delete_that_stays_refused_is_logged_and_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig = await _Rig.create(tmp_path)
    monkeypatch.setattr(runtime_mod, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(runtime_mod, "_DISCARD_RETRY_DELAYS", (0.0,) * 5)
    calls: list[str] = []
    _held_for(rig, 10**6, calls)
    root = tmp_path / "sessions"
    before = _files(root)
    rig.fail = _once(RuntimeError("build boom"))

    with (
        caplog.at_level(logging.WARNING, logger=runtime_mod.__name__),
        pytest.raises(RebuildFailedError) as caught,
    ):
        await rig.runtime.new_session()

    assert caught.value.state == "kept"
    assert len(calls) == len(runtime_mod._DISCARD_RETRY_DELAYS) + 1
    assert len(_files(root)) == len(before) + 1, "the file stays, logged"
    [record] = [r for r in caplog.records if "a failed swap made" in r.getMessage()]
    assert record.exc_info is not None and isinstance(record.exc_info[1], PermissionError)


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX branch runs on POSIX")
async def test_a_refused_delete_off_windows_is_not_retried(tmp_path: Path) -> None:
    """POSIX refuses a delete only for a reason waiting does not change (the folder's mode)."""

    rig = await _Rig.create(tmp_path)
    calls: list[str] = []
    _held_for(rig, 10**6, calls)
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError):
        await rig.runtime.new_session()

    assert len(calls) == 1
