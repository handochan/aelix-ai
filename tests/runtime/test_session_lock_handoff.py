"""#137 / ADR-0244 — the writer lock follows the live session, and a swap
onto a file somebody else owns does not silently happen.

The four public replace APIs are the in-session doors to the same defect the
startup path has: ``/resume`` and ``/import`` can land on a file another
terminal is writing, and ``/new`` and ``/fork`` must let go of the one this
terminal is leaving — otherwise the first terminal to open a session owns it
until the process exits.

A second ``SessionWriterLock`` object in this process is a faithful stand-in
for another terminal: ``fcntl.flock`` and ``LockFileEx`` both scope to the
open file description, not to the process, so the contention is real. The
cross-process and kill-the-holder assertions live in
``tests/session/test_session_writer_lock.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
    SessionError,
    SessionWriterLock,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _factory(new_session: Session) -> AgentHarness:
    return _harness(session=new_session)


async def _runtime_on(
    repo: JsonlSessionRepo, fs: LocalFileSystem, session: Session, *, managed: bool = True
) -> tuple[AgentSessionRuntime, SessionWriterLock | None]:
    runtime = AgentSessionRuntime(_harness(session=session), _factory, repo=repo, fs=fs)
    lock: SessionWriterLock | None = None
    if managed:
        path = session.session_file
        assert path is not None
        lock = SessionWriterLock(path)
        assert lock.try_acquire() is True
        runtime.set_writer_lock(lock)
    return runtime, lock


def _repo(tmp_path: Path) -> tuple[JsonlSessionRepo, LocalFileSystem]:
    fs = LocalFileSystem()
    return JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions")), fs


# === A swap onto a file someone else owns ===================================


async def test_resume_onto_an_owned_file_refuses_when_nothing_can_ask(
    tmp_path: Path,
) -> None:
    """No callback installed → refuse. This is what keeps RPC honest.

    ``rpc_mode`` never installs one, so ``_handle_switch_session`` turns this
    into a structured error on the wire instead of a prompt nobody can answer
    over a JSONL transport whose stdin is already held by a reader thread.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = (await theirs.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)
    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        with pytest.raises(SessionError) as exc:
            await runtime.switch_session(theirs_path)
        assert exc.value.code == "storage"
        assert "another terminal" in str(exc.value)
        assert runtime.session is not None
        assert runtime.session.session_file == mine.session_file, (
            "a refused swap keeps the old session"
        )
    finally:
        other_terminal.release()


async def test_resume_onto_an_owned_file_can_be_cancelled(tmp_path: Path) -> None:
    """Cancel keeps BOTH the old session and its lock.

    A swap that released the old lock and then failed to take the new one
    would leave a live harness writing to a file it no longer owns — which is
    the defect, arrived at from the other direction.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = (await theirs.get_metadata()).path
    mine_path = mine.session_file
    assert mine_path is not None

    runtime, lock = await _runtime_on(repo, fs, mine)
    runtime.set_on_session_contended(lambda _path: _answer("cancel"))
    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(theirs_path)
        assert result.cancelled is True
        assert runtime.session is not None
        assert runtime.session.session_file == mine_path
        assert runtime.writer_lock is lock
        assert SessionWriterLock(mine_path).try_acquire() is False
    finally:
        other_terminal.release()


async def test_resume_onto_an_owned_file_can_fork_the_target(tmp_path: Path) -> None:
    """"Fork and continue" branches the TARGET, not the live session.

    The design left this without a mechanism: ``AgentSessionRuntime.fork`` is
    hard-wired to ``self.session``, so routing the answer through it would
    have branched the wrong conversation. ``repo.fork_from`` is the one that
    takes a source.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    await theirs.append_message(UserMessage(content=[TextContent(text="their turn")]))
    theirs_path = (await theirs.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)
    runtime.set_on_session_contended(lambda _path: _answer("fork"))
    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(theirs_path)
        assert result.cancelled is False
        assert runtime.session is not None
        forked_path = runtime.session.session_file
        assert forked_path is not None
        assert forked_path != theirs_path, "a fork is a different file"

        forked_meta = await runtime.session.get_metadata()
        assert forked_meta.parent_session_path == theirs_path
        texts = [
            part.text
            for entry in await runtime.session.get_entries()
            if entry.type == "message"
            for part in (entry.message.content or [])
            if isinstance(part, TextContent)
        ]
        assert texts == ["their turn"], "the whole conversation came with it"

        # We own the fork, they still own the original.
        assert runtime.writer_lock is not None
        assert runtime.writer_lock.session_path == forked_path
        assert SessionWriterLock(forked_path).try_acquire() is False
    finally:
        other_terminal.release()


async def test_resume_onto_an_owned_file_can_open_it_read_only(tmp_path: Path) -> None:
    """Read-only holds NO lock, and the storage refuses the writes."""

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = (await theirs.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)
    runtime.set_on_session_contended(lambda _path: _answer("read_only"))
    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(theirs_path)
        assert result.cancelled is False
        assert runtime.session is not None
        assert runtime.session.session_file == theirs_path
        assert runtime.writer_lock is None, "a viewer owns nothing"
        with pytest.raises(SessionError) as exc:
            await runtime.session.append_message(
                UserMessage(content=[TextContent(text="nope")])
            )
        assert exc.value.code == "read_only"
    finally:
        other_terminal.release()


# === The lock follows the live session ======================================


async def test_resume_onto_a_free_file_moves_the_lock(tmp_path: Path) -> None:
    """And the session we walked away from becomes openable by someone else."""

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_path = (await target.get_metadata()).path
    mine_path = mine.session_file
    assert mine_path is not None

    runtime, _lock = await _runtime_on(repo, fs, mine)
    assert SessionWriterLock(mine_path).try_acquire() is False

    result = await runtime.switch_session(target_path)
    assert result.cancelled is False
    assert runtime.writer_lock is not None
    assert runtime.writer_lock.session_path == target_path

    released = SessionWriterLock(mine_path)
    assert released.try_acquire() is True, "the old file is free again"
    released.release()
    assert SessionWriterLock(target_path).try_acquire() is False


@pytest.mark.parametrize("door", ["new", "fork"])
async def test_new_and_fork_move_the_lock_onto_the_file_they_create(
    tmp_path: Path, door: str
) -> None:
    """Brand-new files cannot contend, but the lock must still MOVE."""

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    await mine.append_message(UserMessage(content=[TextContent(text="hi")]))
    entry_id = (await mine.get_entries())[-1].id
    mine_path = mine.session_file
    assert mine_path is not None

    runtime, _lock = await _runtime_on(repo, fs, mine)
    if door == "new":
        result = await runtime.new_session()
    else:
        result = await runtime.fork(entry_id, position="before")
    assert result.cancelled is False

    assert runtime.session is not None
    new_path = runtime.session.session_file
    assert new_path is not None
    assert new_path != mine_path
    assert runtime.writer_lock is not None
    assert runtime.writer_lock.session_path == new_path

    freed = SessionWriterLock(mine_path)
    assert freed.try_acquire() is True
    freed.release()


async def test_dispose_releases_the_lock(tmp_path: Path) -> None:
    """``/quit`` frees the file while this process is still alive.

    The kernel would drop it at exit anyway; this is what makes a second
    terminal usable straight after the first one quits, rather than after it
    finishes shutting down.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    path = mine.session_file
    assert path is not None

    runtime, _lock = await _runtime_on(repo, fs, mine)
    assert SessionWriterLock(path).try_acquire() is False
    await runtime.dispose()
    assert runtime.writer_lock is None
    after = SessionWriterLock(path)
    assert after.try_acquire() is True
    after.release()


# === Import =================================================================


async def test_import_refuses_to_overwrite_an_owned_destination(
    tmp_path: Path,
) -> None:
    """``copy_file`` REPLACES its destination, so the check runs before it.

    Re-importing a file that was already imported here targets the same
    ``<sessions root>/<encoded cwd>/<basename>`` — which another terminal may
    be writing to right now. Asking afterwards would be asking about a session
    that is already gone.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime, _lock = await _runtime_on(repo, fs, mine)

    # The destination the import will compute, populated and owned.
    session_dir = await repo._get_session_dir(str(tmp_path))
    destination = Path(session_dir) / "imported.jsonl"
    source = tmp_path / "imported.jsonl"
    source.write_text(
        (Path(mine.session_file or "").read_text(encoding="utf-8")), encoding="utf-8"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("THE OTHER TERMINAL'S FILE\n", encoding="utf-8")

    other_terminal = SessionWriterLock(str(destination))
    assert other_terminal.try_acquire() is True
    try:
        with pytest.raises(SessionError) as exc:
            await runtime.import_from_jsonl(str(source))
        assert exc.value.code == "storage"
        assert destination.read_text(encoding="utf-8") == "THE OTHER TERMINAL'S FILE\n"
    finally:
        other_terminal.release()


# === Opting out =============================================================


async def test_a_runtime_nobody_handed_a_lock_takes_none(tmp_path: Path) -> None:
    """Embedders and tests do not start enforcing ownership by accident.

    ``set_writer_lock`` is what opts in. Without it no sidecar is created, so
    a library that drives the runtime in-process does not litter a user's
    session directory with files it will never release.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_path = (await target.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine, managed=False)
    other_terminal = SessionWriterLock(target_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(target_path)
        assert result.cancelled is False, "no ownership enforcement, no refusal"
        assert runtime.writer_lock is None
    finally:
        other_terminal.release()
    mine_path = mine.session_file
    assert mine_path is not None
    assert not Path(f"{mine_path}.lock").exists(), "no sidecar for a file we never owned"


async def _answer(choice: str) -> Any:
    return choice


# === What review found: a swap that fails must not strand the target lock ===


async def test_a_swap_that_dies_before_the_harness_moves_frees_the_target(
    tmp_path: Path,
) -> None:
    """The lock a failed swap took is released, not left in the kernel.

    Every replace API took the target's lock, ran several awaits that can
    raise, and only adopted it if they all returned. On a raise the
    :class:`SessionWriterLock` was neither released nor stored — the kernel
    held it for the life of the process and no object was left that could let
    go. Measured: two ``FileLock``s on one path inside one process are
    mutually exclusive under ``fcntl.flock``, so the *retry* of the same
    command was then told the file was "open in another terminal" — about
    this process's own orphan. No other terminal could open it either.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_path = (await target.get_metadata()).path
    mine_path = mine.session_file
    assert mine_path is not None

    runtime, _lock = await _runtime_on(repo, fs, mine)

    boom = RuntimeError("the cwd went away")

    async def _explode(*_a: object, **_k: object) -> None:
        raise boom

    # The first await inside ``_finish_session_replacement`` that can raise
    # with the process still on the OLD session.
    runtime._finish_session_replacement = _explode  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        await runtime.switch_session(target_path)

    assert runtime.session is not None
    assert runtime.session.session_file == mine_path, "still on the old session"
    assert runtime.writer_lock is not None
    assert runtime.writer_lock.session_path == mine_path, "still holding its lock"

    retry = SessionWriterLock(target_path)
    assert retry.try_acquire() is True, "the target's lock was not orphaned"
    retry.release()


async def test_a_cancelled_swap_frees_the_lock_it_took(tmp_path: Path) -> None:
    """An extension's ``session_before_switch`` cancel is the common case.

    Ownership is now taken BEFORE the file is read (so no turn can slip in
    between the read and the lock), which puts it before the cancel hook —
    and a cancelled swap must hand the target back exactly as it found it.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_path = (await target.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)

    async def _cancel(*_a: object, **_k: object) -> bool:
        return True

    runtime._emit_before_switch = _cancel  # type: ignore[assignment]
    result = await runtime.switch_session(target_path)
    assert result.cancelled is True

    freed = SessionWriterLock(target_path)
    assert freed.try_acquire() is True, "a cancelled swap owns nothing"
    freed.release()
    assert runtime.writer_lock is not None
    assert runtime.writer_lock.session_path == mine.session_file


async def test_a_failed_import_frees_the_destination(tmp_path: Path) -> None:
    """``/import`` of a file whose recorded cwd is gone, then a retry.

    ``import_from_jsonl`` locks the destination before the copy — deliberately,
    because the copy replaces its destination wholesale. Five awaits after
    that can raise, and the retry the user naturally types next has to work.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime, _lock = await _runtime_on(repo, fs, mine)

    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path / "gone")))
    source_path = (await source.get_metadata()).path
    external = tmp_path / "backup.jsonl"
    external.write_bytes(Path(source_path).read_bytes())

    from aelix_agent_core.session.session_cwd import MissingSessionCwdError

    with pytest.raises(MissingSessionCwdError):
        await runtime.import_from_jsonl(str(external))

    destination = SessionWriterLock(
        str(Path(await repo._get_session_dir(str(tmp_path))) / "backup.jsonl")
    )
    assert destination.try_acquire() is True, "the destination lock was released"
    destination.release()

    # And the retry the user naturally types next reports the SAME failure —
    # never "cannot import over a session that is open in another terminal".
    with pytest.raises(MissingSessionCwdError):
        await runtime.import_from_jsonl(str(external))
    assert runtime.writer_lock is not None
    assert runtime.writer_lock.session_path == mine.session_file


async def test_the_fork_arm_tells_extensions_the_file_it_actually_moved_to(
    tmp_path: Path,
) -> None:
    """``session_shutdown``'s ``target_session_file`` is the FORK, not the
    contended original.

    The fork arm swaps in a different file, but the original path was still
    handed to ``_finish_session_replacement`` — so an extension that mirrors
    the transcript path, or a usage tracker keyed on it, recorded this process
    as having switched to a file the other terminal still owns and is still
    appending to.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = (await theirs.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)
    runtime.set_on_session_contended(lambda _path: _answer("fork"))

    seen: list[str | None] = []
    original_teardown = runtime._teardown_current

    async def _record(reason: str, target_session_file: str | None = None) -> None:
        seen.append(target_session_file)
        await original_teardown(reason, target_session_file)  # type: ignore[arg-type]

    runtime._teardown_current = _record  # type: ignore[assignment]

    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(theirs_path)
        assert result.cancelled is False
        assert runtime.session is not None
        forked_path = runtime.session.session_file
        assert seen == [forked_path]
        assert theirs_path not in seen, "not the file we deliberately did not take"
    finally:
        other_terminal.release()


async def test_a_brand_new_file_that_is_owned_fails_closed(tmp_path: Path) -> None:
    """``/new`` racing another terminal's ``--continue`` onto the new file.

    A microsecond window, but the failure direction was fail-OPEN and silent:
    the ``None`` meaning "someone owns it" was indistinguishable from the
    ``None`` meaning "no ownership to move", so the runtime released the old
    lock, installed nothing, and carried on appending to a file it did not own.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    mine_path = mine.session_file
    assert mine_path is not None
    runtime, _lock = await _runtime_on(repo, fs, mine)

    stolen: list[SessionWriterLock] = []
    original_create = repo.create

    async def _create_then_steal(options: Any) -> Session:
        created = await original_create(options)
        path = created.session_file
        assert path is not None
        thief = SessionWriterLock(path)
        assert thief.try_acquire() is True
        stolen.append(thief)
        return created

    repo.create = _create_then_steal  # type: ignore[assignment]
    try:
        with pytest.raises(SessionError) as exc:
            await runtime.new_session()
        assert "already open in another terminal" in str(exc.value)
        assert runtime.session is not None
        assert runtime.session.session_file == mine_path, "we kept our own session"
        assert runtime.writer_lock is not None
        assert runtime.writer_lock.session_path == mine_path, "…and its lock"
    finally:
        for lock in stolen:
            lock.release()


async def test_a_read_only_swap_does_not_write_the_thinking_level(
    tmp_path: Path,
) -> None:
    """The read-only arm hands ``_finish_session_replacement`` a storage that
    refuses writes, and it appended a ``thinking_level_change`` to it.

    That raise lands at the one point where the old harness is already
    disposed and the new one is already live — a half-swap, with no lock
    installed and no rebind done. The startup path guards the identical write
    (``_seed_startup_state(read_only=…)``); this is its in-session twin.
    """

    repo, fs = _repo(tmp_path)
    mine = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = (await theirs.get_metadata()).path

    runtime, _lock = await _runtime_on(repo, fs, mine)
    runtime.harness._state.thinking_level = "high"
    runtime.set_on_session_contended(lambda _path: _answer("read_only"))

    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        result = await runtime.switch_session(theirs_path)
        assert result.cancelled is False
        assert runtime.session is not None
        assert runtime.session.session_file == theirs_path
        assert not [
            e
            for e in await runtime.session.get_entries()
            if e.type == "thinking_level_change"
        ], "nothing was written into a file we do not own"
    finally:
        other_terminal.release()
