"""#137 / ADR-0244 — what the second terminal is told, and what it gets.

``_resolve_session_ownership`` is the one place every session-opening flag
passes through, so this file is the product-band contract for D1: a terminal
that can ask is asked; one that cannot is refused; and the answer it gives is
honoured all the way down to whether its storage will accept an append.

The cross-process half (a real second ``aelix`` refused on stderr with exit 1)
is measured in the commit message; the kernel half is
``tests/session/test_session_writer_lock.py``. Here a second
``SessionWriterLock`` object stands in for the other terminal — the same
stand-in, and for the same reason, as ``tests/runtime/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    ReadOnlySessionStorage,
    Session,
    SessionError,
    SessionWriterLock,
)
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_ai.messages import TextContent, UserMessage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.entry import (
    _resolve_session_ownership,
    _seed_startup_state,
    _session_owned_message,
)


async def _session(tmp_path: Path) -> tuple[JsonlSessionRepo, Session, str]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    path = session.session_file
    assert path is not None
    return repo, session, path


def _answer_with(
    monkeypatch: pytest.MonkeyPatch, choice: str, seen: dict[str, Any] | None = None
) -> None:
    async def _prompt(path: str, *, default_read_only: bool) -> str:
        if seen is not None:
            seen["path"] = path
            seen["default_read_only"] = default_read_only
        return choice

    monkeypatch.setattr(entry_mod, "_prompt_session_owned_interactive", _prompt)


# === The uncontended cases: nobody is asked anything ========================


async def test_an_unowned_session_is_locked_and_handed_back(tmp_path: Path) -> None:
    """The overwhelmingly common path: one terminal, one session."""

    repo, session, path = await _session(tmp_path)
    resolved = await _resolve_session_ownership(
        session,
        repo,
        str(tmp_path),
        app_mode="interactive",
        named_exactly=False,
        opened_existing=False,
    )
    assert resolved is not None
    got, lock, read_only = resolved
    assert got is session
    assert read_only is False
    assert lock is not None and lock.held is True
    assert SessionWriterLock(path).try_acquire() is False
    lock.release()


async def test_no_session_has_nothing_to_own(tmp_path: Path) -> None:
    """``--no-session`` is in-memory storage — no file, no lock, no sidecar."""

    repo, _owned, _path = await _session(tmp_path)
    resolved = await _resolve_session_ownership(
        Session(MemorySessionStorage()),
        repo,
        str(tmp_path),
        app_mode="print",
        named_exactly=False,
        opened_existing=False,
    )
    assert resolved is not None
    _got, lock, read_only = resolved
    assert lock is None
    assert read_only is False
    assert not list((tmp_path / "sessions").rglob("*.lock"))


# === Non-interactive: refuse ================================================


@pytest.mark.parametrize("app_mode", ["print", "json", "rpc"])
async def test_a_non_interactive_run_is_refused_with_the_way_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], app_mode: str
) -> None:
    """A process that cannot ask must not choose data loss on the user's behalf.

    All three non-interactive modes take the same arm: ``-p``, ``--mode json``
    and ``--mode rpc``. RPC especially — its transport IS stdin/stdout JSONL,
    so a full-screen selector there would paint ANSI into the response stream
    and block on a stdin nothing will ever write to.
    """

    repo, session, path = await _session(tmp_path)
    other_terminal = SessionWriterLock(path)
    assert other_terminal.try_acquire() is True
    try:
        resolved = await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode=app_mode,
            named_exactly=False,
            opened_existing=False,
        )
        assert resolved is None, "the caller exits 1"
        err = capsys.readouterr().err
        assert "already open in another terminal" in err
        assert f"--fork {path}" in err
        assert "--session <other>" in err
        assert "--no-session" in err
    finally:
        other_terminal.release()


def test_the_refusal_and_the_cancel_arm_say_the_same_thing(tmp_path: Path) -> None:
    """One string, two call sites — they cannot drift."""

    message = _session_owned_message(str(tmp_path / "s.jsonl"))
    assert message.startswith("Error: this session is already open in another terminal.")
    assert message.count(str(tmp_path / "s.jsonl")) == 2


# === Interactive: the three answers =========================================


async def test_cancel_exits_and_leaves_the_file_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, session, path = await _session(tmp_path)
    _answer_with(monkeypatch, "cancel")
    other_terminal = SessionWriterLock(path)
    assert other_terminal.try_acquire() is True
    try:
        resolved = await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode="interactive",
            named_exactly=False,
            opened_existing=False,
        )
        assert resolved is None
        assert "already open in another terminal" in capsys.readouterr().err
    finally:
        other_terminal.release()


async def test_read_only_takes_no_lock_and_refuses_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer sees the session; it cannot add to it.

    No lock at all, deliberately: a shared-reader lock would exclude the
    legitimate owner, which is the one situation this option exists for.
    """

    repo, session, path = await _session(tmp_path)
    _answer_with(monkeypatch, "read_only")
    other_terminal = SessionWriterLock(path)
    assert other_terminal.try_acquire() is True
    try:
        resolved = await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode="interactive",
            named_exactly=True,
            opened_existing=False,
        )
        assert resolved is not None
        viewer, lock, read_only = resolved
        assert read_only is True
        assert lock is None
        assert isinstance(viewer.get_storage(), ReadOnlySessionStorage)
        assert viewer.session_file == path, "the viewer still knows which file it is"
        with pytest.raises(SessionError) as exc:
            await viewer.append_message(UserMessage(content=[TextContent(text="no")]))
        assert exc.value.code == "read_only"
    finally:
        other_terminal.release()


async def test_fork_and_continue_gives_a_new_file_that_we_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new file with the same history, lineage-linked, and lockable.

    The original stays the first terminal's. The fork is newest by mtime, so
    the next ``--continue`` here lands on it — which only holds because #297
    stopped the header sniff from dropping a fork header over 512 bytes, and
    that is checked here rather than assumed.
    """

    repo, session, path = await _session(tmp_path)
    await session.append_message(UserMessage(content=[TextContent(text="shared history")]))
    _answer_with(monkeypatch, "fork")
    other_terminal = SessionWriterLock(path)
    assert other_terminal.try_acquire() is True
    try:
        resolved = await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode="interactive",
            named_exactly=False,
            opened_existing=False,
        )
        assert resolved is not None
        forked, lock, read_only = resolved
        assert read_only is False
        forked_path = forked.session_file
        assert forked_path is not None and forked_path != path
        assert lock is not None and lock.session_path == forked_path
        assert lock.held is True

        metadata = await forked.get_metadata()
        assert metadata.parent_session_path == path
        texts = [
            part.text
            for e in await forked.get_entries()
            if e.type == "message"
            for part in (e.message.content or [])
            if isinstance(part, TextContent)
        ]
        assert texts == ["shared history"]

        # We can write to the fork, and the original is untouched.
        await forked.append_message(UserMessage(content=[TextContent(text="mine")]))
        assert "shared history" in Path(path).read_text(encoding="utf-8")
        assert "mine" not in Path(path).read_text(encoding="utf-8")

        # And the next ``--continue`` in this cwd finds the fork (#297).
        most_recent = await repo.find_most_recent(str(tmp_path))
        assert most_recent is not None
        assert most_recent.path == forked_path

        assert f"Forked into a new session: {forked_path}" in capsys.readouterr().err
        lock.release()
    finally:
        other_terminal.release()


async def test_the_default_cursor_depends_on_whether_the_file_was_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--session <file>`` asked for THAT file; ``--continue`` asked for "here".

    One constant, threaded through as ``named_exactly``. Checked here rather
    than in the widget because the widget is a ``prompt_toolkit.Application``
    and the decision is not.
    """

    repo, session, path = await _session(tmp_path)
    seen: dict[str, Any] = {}
    _answer_with(monkeypatch, "cancel", seen)
    other_terminal = SessionWriterLock(path)
    assert other_terminal.try_acquire() is True
    try:
        await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode="interactive",
            named_exactly=False,
            opened_existing=False,
        )
        assert seen["default_read_only"] is False
        assert seen["path"] == path
        await _resolve_session_ownership(
            session,
            repo,
            str(tmp_path),
            app_mode="interactive",
            named_exactly=True,
            opened_existing=False,
        )
        assert seen["default_read_only"] is True
    finally:
        other_terminal.release()


async def test_no_answer_is_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Esc, Ctrl-C and a missing ``[tui]`` extra all reach the same place.

    ``_prompt_one_shot_select`` returns ``None`` for all three, and the
    ownership prompt maps that to cancel — the same fail-closed rule the
    project-trust gate uses, for the same reason: "no answer" may never be
    read as consent to write into a file someone else owns.
    """

    async def _no_widget(body: str, options: list[str]) -> str | None:
        return None

    monkeypatch.setattr(entry_mod, "_prompt_one_shot_select", _no_widget)
    choice = await entry_mod._prompt_session_owned_interactive(
        "/x/y.jsonl", default_read_only=False
    )
    assert choice == "cancel"


# === The write the startup path makes on its own ============================


async def test_thinking_flag_does_not_write_into_a_read_only_session(
    tmp_path: Path,
) -> None:
    """``aelix --session <owned file> --thinking high`` must still open.

    ``_seed_startup_state`` RECORDS a ``--thinking`` level into a session that
    has none, and it is called from ``_async_main`` at function-body
    indentation — outside every ``try``. Unguarded, the
    ``SessionError("read_only")`` would come out as a traceback and the viewer
    would never appear. The flag still governs this launch; only the record is
    suppressed.
    """

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_ai.streaming import Model

    _unused_repo, session, _path = await _session(tmp_path)
    viewer = Session(ReadOnlySessionStorage(session.get_storage()))
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="mock", provider="mock"), session=viewer)
    )

    applied = await _seed_startup_state(harness, viewer, cli_level="high", read_only=True)
    assert applied is True, "the flag still governs this launch"
    assert not [e for e in await viewer.get_entries() if e.type == "thinking_level_change"]

    # …and the same call on a writable session DOES record it, so the guard is
    # not quietly disabling the feature.
    assert await _seed_startup_state(harness, session, cli_level="high") is True
    assert [e for e in await session.get_entries() if e.type == "thinking_level_change"]


# === The window between reading the file and owning it ======================


async def test_a_turn_written_during_the_handoff_survives(tmp_path: Path) -> None:
    """#137's own symptom, at the seam the lock was supposed to close.

    Startup reads the session first and takes the lock second, and the gap
    between the two is exactly the handoff ``DEFAULT_ACQUIRE_TIMEOUT`` exists
    to absorb: the other terminal appends its last turn and *then* releases,
    on ``/new`` or ``/quit``. This terminal wins the lock a few milliseconds
    later while still holding the process-local leaf from before that append —
    and its next append reparents onto that stale leaf, so the other
    terminal's final turn is gone from the replay. Reviewed and measured as
    "stored: shared, A-final, B-next; replay: shared, B-next".

    Re-reading under the lock is what closes it: past that line nobody else
    may append.
    """

    repo, session_a, path = await _session(tmp_path)
    await session_a.append_message(UserMessage(content=[TextContent(text="shared history")]))

    # This terminal loads the file…
    meta = await session_a.get_metadata()
    session_b = await repo.open(meta, cwd_override=str(tmp_path))
    loaded_by_b = len(await session_b.get_entries())

    # …and the other terminal writes its final turn before letting go.
    await session_a.append_message(UserMessage(content=[TextContent(text="A's final turn")]))

    resolved = await _resolve_session_ownership(
        session_b,
        repo,
        str(tmp_path),
        app_mode="interactive",
        named_exactly=False,
        opened_existing=True,
    )
    assert resolved is not None
    owned, lock, read_only = resolved
    assert lock is not None and lock.held is True
    assert read_only is False
    try:
        texts = [
            c.text
            for e in await owned.get_entries()
            if e.type == "message"
            for c in getattr(e.message, "content", [])  # type: ignore[union-attr]
            if isinstance(c, TextContent)
        ]
        assert "A's final turn" in texts, "the handoff turn is not lost"
        assert len(await owned.get_entries()) > loaded_by_b

        # And the branch we would append onto is the one that ends at A's last
        # turn — the leaf, not merely the entry list, is what #137 corrupts.
        await owned.append_message(UserMessage(content=[TextContent(text="B's next turn")]))
        branch = [
            c.text
            for e in await owned.get_branch()
            if e.type == "message"
            for c in getattr(e.message, "content", [])  # type: ignore[union-attr]
            if isinstance(c, TextContent)
        ]
        assert branch == ["shared history", "A's final turn", "B's next turn"]
    finally:
        lock.release()


async def test_a_freshly_created_session_is_not_re_read(tmp_path: Path) -> None:
    """The other three flags publish the file themselves, so there is no race.

    ``opened_existing`` is the whole difference: a bare launch and ``--fork``
    hand back the very object they created, and pay no second read for it.
    """

    repo, session, _path = await _session(tmp_path)
    resolved = await _resolve_session_ownership(
        session,
        repo,
        str(tmp_path),
        app_mode="interactive",
        named_exactly=False,
        opened_existing=False,
    )
    assert resolved is not None
    got, lock, _read_only = resolved
    assert got is session
    assert lock is not None
    lock.release()


# === A sidecar we cannot use is not a filesystem that cannot lock ===========


async def test_an_unusable_lock_file_refuses_with_the_real_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 1 naming the sidecar — not "already open in another terminal".

    A root-owned ``<session>.jsonl.lock`` (``sudo aelix`` made it once) or a
    sessions directory shared across accounts reaches filelock as a plain
    ``OSError``. That used to be read as "this filesystem cannot lock", which
    degraded — handing this terminal the session while its real owner kept
    appending. Fail closed, and say which file to look at.
    """

    import aelix_agent_core.session.session_lock as session_lock

    class _BrokenSidecar:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(session_lock, "FileLock", _BrokenSidecar)
    repo, session, path = await _session(tmp_path)
    resolved = await _resolve_session_ownership(
        session,
        repo,
        str(tmp_path),
        app_mode="interactive",
        named_exactly=False,
        opened_existing=True,
    )
    assert resolved is None, "the caller exits 1"
    err = capsys.readouterr().err
    assert "lock file cannot be used" in err
    assert f"{path}.lock" in err
    assert "already open in another terminal" not in err
