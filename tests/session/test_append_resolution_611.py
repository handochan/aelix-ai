"""A write whose outcome is unknown is settled before the next parent is chosen (#721).

#611's S1a, Codex round 5 findings 3 and 4. ``Session`` read the leaf and built
the entry before it reached the store, so after an append whose line landed and
then raised, the next entry was parented on the leaf from before that line: a
sibling. The landed line stayed in the file, off the current branch — on resume
it was neither in the conversation nor written again.

Measured on main ``fc8f3048``, the first case below run as a script against
the real ``Session`` and loader::

    {"probe": "sibling-parent", "raised": "SessionError:storage",
     "whole_file_notices": 1, "branch_notices": 0, "branch_types": ["message", "message"]}

The store now keeps such an entry pending, and the next write reads the file
back under the store's lock, takes the entry in if its line is there, and only
then builds the next entry on the leaf (ADR-0242 rule 5). The failure classes
those cases need apart: :class:`NotAttempted` (nothing written, retryable),
any other ``OSError`` (``outcome_unknown``), a cancel or an interrupt (raised as
itself), and an entry that cannot be encoded (``invalid_entry``, as before).
"""

from __future__ import annotations

import asyncio
import errno
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    NotAttempted,
    PlacementConflict,
    ReadOnlySessionStorage,
    Session,
    SessionError,
)
from aelix_agent_core.session import fs as fs_mod
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent

from tests.session.test_jsonl_write_discipline import _OsProxy, _YieldingFs


class _PlanFs(LocalFileSystem):
    """The next ``append_file`` writes ``keep(content)`` and then raises ``error``.

    ``reads`` counts ``read_text_file`` calls, which is how a case sees whether
    a write settled an outcome: only a pending entry makes the store read the
    file back. ``fail_reads`` makes those reads raise. ``skip`` lets that many
    appends through first, for a case aimed at the second write of an operation.
    """

    def __init__(self) -> None:
        super().__init__()
        self._plan: tuple[Callable[[str], str], BaseException] | None = None
        self._skip = 0
        self.reads = 0
        self.fail_reads = False

    def arm(self, keep: Callable[[str], str], error: BaseException, *, skip: int = 0) -> None:
        self._plan = (keep, error)
        self._skip = skip

    async def append_file(self, path: str, content: str) -> None:
        if self._plan is None or self._skip:
            self._skip = max(0, self._skip - 1)
            await super().append_file(path, content)
            return
        keep, error = self._plan
        self._plan = None
        written = keep(content)
        if written:
            await super().append_file(path, written)
        raise error

    async def read_text_file(self, path: str) -> str:
        self.reads += 1
        if self.fail_reads:
            raise OSError(errno.EIO, "read refused (injected)")
        return await super().read_text_file(path)


def _whole(content: str) -> str:
    return content


def _half(content: str) -> str:
    return content[: len(content) // 2]


def _enospc() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device (injected)")


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _text(entry: Any) -> str | None:
    message = getattr(entry, "message", None)
    if message is None:
        return getattr(entry, "content", None)
    return message.content[0].text


async def _seeded(tmp_path: Path) -> tuple[Session, _PlanFs, Path, str]:
    fs = _PlanFs()
    path = tmp_path / "s.jsonl"
    storage = await JsonlSessionStorage.create(fs, str(path), cwd=str(tmp_path), session_id="r")
    session = Session(storage)
    seed = await session.append_message(_user("seed"))
    return session, fs, path, seed


async def _reopened_branch(path: Path) -> list[str | None]:
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    return [_text(e) for e in await reopened.get_branch()]


# === finding 3: the parent is chosen after the outcome is settled =================


async def test_entry_after_landed_then_raised_append_is_parented_on_the_landed_entry(
    tmp_path: Path,
) -> None:
    """Codex r5 probe 1's shape, in place: the same Session object goes on writing.

    On main the notice is in the file and off the branch (``branch_notices=0``).
    """

    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError) as exc:
        await session.append_custom_message_entry("aelix.job_notice", "notice", True, {"n": 1})
    assert exc.value.code == "outcome_unknown"

    after = await session.append_message(_user("next input"))

    notice = next(e for e in await session.get_entries() if e.type == "custom_message")
    assert notice.parent_id == seed
    assert (await session.get_entry(after)).parent_id == notice.id  # type: ignore[union-attr]
    assert await _reopened_branch(path) == ["seed", "notice", "next input"]


async def test_a_line_that_did_not_land_whole_is_dropped_and_costs_nothing(
    tmp_path: Path,
) -> None:
    """Half a line, then ENOSPC: not the loader's entry, so not taken in. The next
    entry follows the seed, the fragment is its own skipped line, nothing is pruned."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(_half, _enospc())
    with pytest.raises(SessionError) as exc:
        await session.append_message(_user("torn"))
    assert exc.value.code == "outcome_unknown"

    await session.append_message(_user("after"))

    assert [_text(e) for e in await session.get_branch()] == ["seed", "after"]
    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert reopened.recovery is not None
    assert reopened.recovery.orphaned_entries == ()
    assert await _reopened_branch(path) == ["seed", "after"]


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (OSError(errno.ENOSPC, "No space left on device (injected)"), SessionError),
        (OSError(errno.EIO, "I/O error (injected)"), SessionError),
        (asyncio.CancelledError("injected"), asyncio.CancelledError),
        (KeyboardInterrupt(), KeyboardInterrupt),
        (SystemExit(17), SystemExit),
    ],
    ids=["ENOSPC", "EIO", "cancel", "KeyboardInterrupt", "SystemExit"],
)
async def test_a_whole_line_whose_final_newline_was_lost_is_followed_by_the_next_entry(
    tmp_path: Path, error: BaseException, raised: type[BaseException]
) -> None:
    """The CHANGELOG's own example, through ``Session.append_message``: every byte
    of the line but its final newline reached the file, then the write raised.
    The line is whole to the loader, so it landed. Codex's no-newline rows, one
    per error kind; with the line matched including its newline (the verify
    round's mutant R1) each read ``[seed, after]`` with ``attempt`` left in the
    file, the #721 bug again."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(lambda content: content[:-1], error)
    with pytest.raises(raised) as exc:
        await session.append_message(_user("attempt"))
    if raised is SessionError:
        assert exc.value.code == "outcome_unknown"  # type: ignore[attr-defined]
    else:
        assert exc.value is error, "re-raised as itself"
    assert not path.read_text().endswith("\n"), "the line's newline is what was lost"

    await session.append_message(_user("after"))

    expected = ["seed", "attempt", "after"]
    assert [_text(e) for e in await session.get_branch()] == expected
    assert await _reopened_branch(path) == expected
    assert (await _texts_in_file(path)).count("attempt") == 1, "taken in, not written again"


@pytest.mark.parametrize(
    "write",
    ["model_change", "thinking_level_change", "custom", "session_info", "compaction"],
)
async def test_every_append_kind_is_built_after_the_outcome_is_settled(
    tmp_path: Path, write: str
) -> None:
    """Each ``Session.append_*`` goes through ``append_to_leaf``, not only messages."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    storage = session.get_storage()  # the store's own view; Session's reads settle first
    unsettled = await storage.get_path_to_root(await storage.get_leaf_id())
    assert [_text(e) for e in unsettled] == ["seed"], "not in memory yet"

    calls: dict[str, Callable[[], Any]] = {
        "model_change": lambda: session.append_model_change("p", "m"),
        "thinking_level_change": lambda: session.append_thinking_level_change("high"),
        "custom": lambda: session.append_custom_entry("x.record", {"k": 1}),
        "session_info": lambda: session.append_session_name("named"),
        "compaction": lambda: session.append_compaction("sum", "nope", 1),
    }
    new_id = await calls[write]()

    entry = await session.get_entry(new_id)
    assert entry is not None and entry.type == write
    parent = await session.get_entry(entry.parent_id)  # type: ignore[arg-type]
    assert _text(parent) == "landed"
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    assert [e.id for e in await reopened.get_branch()][-1] == new_id
    assert [_text(e) for e in await reopened.get_branch()][:2] == ["seed", "landed"]


async def test_a_label_on_a_landed_then_raised_entry_finds_its_target(tmp_path: Path) -> None:
    session, fs, _path, _seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    target = next(  # read from the file: memory has not taken it in yet
        e.id
        for e in await (await JsonlSessionStorage.open(LocalFileSystem(), str(_path))).get_entries()
        if _text(e) == "landed"
    )

    await session.append_label(target, "keep")

    assert await session.get_label(target) == "keep"


async def test_move_to_a_landed_then_raised_entry_keeps_that_parent(tmp_path: Path) -> None:
    """An explicit-parent operation settles first and is never redirected."""

    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    landed = next(
        e.id
        for e in await (await JsonlSessionStorage.open(LocalFileSystem(), str(path))).get_entries()
        if _text(e) == "landed"
    )

    summary_id = await session.move_to(seed, {"summary": "back to the seed"})

    summary = await session.get_entry(summary_id)  # type: ignore[arg-type]
    assert summary is not None and summary.parent_id == seed
    assert await session.get_entry(landed) is not None, "taken in before the move"
    await session.move_to(landed)
    assert await session.get_leaf_id() == landed


async def test_withdraw_tail_reads_the_path_after_the_outcome_is_settled(tmp_path: Path) -> None:
    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("turn"))
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_custom_entry("x.neighbour", {"k": 1})

    await session.withdraw_tail(seed, lambda e: e.type == "message")

    branch = await session.get_branch()
    assert [e.type for e in branch] == ["message", "custom"]
    assert branch[1].parent_id == seed
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    assert [e.type for e in await reopened.get_branch()] == ["message", "custom"]


async def test_set_leaf_id_settles_first_and_its_leaf_line_follows_the_landed_entry(
    tmp_path: Path,
) -> None:
    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))

    await session.get_storage().set_leaf_id(seed)

    leaf_line = (await session.get_entries())[-1]
    assert leaf_line.type == "leaf"
    assert _text(await session.get_entry(leaf_line.parent_id)) == "landed"  # type: ignore[arg-type]
    assert await _reopened_branch(path) == ["seed"]


async def test_concurrent_session_appends_chain_on_one_another(tmp_path: Path) -> None:
    """The same rule without a failure: the leaf is read in the same hold as the
    write, so appends from several tasks at once form one chain. On main each
    read the leaf before taking the lock, and a file system that yields mid-line
    (``_YieldingFs``) left them all siblings of the seed."""

    path = tmp_path / "s.jsonl"
    session = Session(
        await JsonlSessionStorage.create(_YieldingFs(), str(path), cwd=str(tmp_path), session_id="c")
    )
    seed = await session.append_message(_user("seed"))

    ids = await asyncio.gather(*(session.append_message(_user(f"m{i}")) for i in range(8)))

    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    branch = [e.id for e in await reopened.get_branch()]
    assert branch[0] == seed and sorted(branch[1:]) == sorted(ids), "every append on the branch"
    assert len(branch) == 9


# === withdraw_tail is one operation (Codex review of #721, finding 1) =============


async def _texts_in_file(path: Path) -> list[str | None]:
    """Every entry's text, in file order, read back by the loader."""

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    return [_text(e) for e in await storage.get_entries()]


async def test_a_withdrawal_whose_leaf_move_landed_and_raised_keeps_its_survivors(
    tmp_path: Path,
) -> None:
    """Codex's ``withdraw-leaf-failure``: the leaf move's line lands, then EIO. On
    ``ed76ea76`` the survivors were never appended, the next write adopted the
    moved leaf, and the branch read ``[seed, after failure]`` (base ``fc8f3048``
    kept them). The next write now finishes the withdrawal first."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("must survive"))
    fs.arm(_whole, OSError(errno.EIO, "leaf landed then raised (injected)"))
    with pytest.raises(SessionError) as exc:
        await session.withdraw_tail(seed, lambda _e: False)
    assert exc.value.code == "outcome_unknown"

    await session.append_message(_user("after failure"))

    expected = ["seed", "must survive", "after failure"]
    assert [_text(e) for e in await session.get_branch()] == expected
    assert await _reopened_branch(path) == expected


async def test_a_withdrawal_whose_leaf_move_did_not_land_did_not_happen(tmp_path: Path) -> None:
    """Half of the leaf move's line: it never reached the file, so nothing was
    withdrawn and nothing is owed. The branch is the one it started from."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    await session.append_custom_entry("x.neighbour", {"k": 1})
    fs.arm(_half, _enospc())
    with pytest.raises(SessionError):
        await session.withdraw_tail(seed, lambda e: e.type == "message")

    await session.append_message(_user("after"))

    expected = ["seed", "the turn", None, "after"]
    assert [_text(e) for e in await session.get_branch()] == expected
    assert await _reopened_branch(path) == expected


async def test_a_withdrawal_whose_leaf_move_was_not_attempted_owes_nothing(
    tmp_path: Path,
) -> None:
    """``NotAttempted`` on the leaf move itself: nothing was written, nothing is
    pending, and the withdrawal did not happen, so nothing is owed. Every later
    write goes through, the same withdrawal again included. With a replay owed
    anyway (the verify round's mutant R5) every one of them died on an
    ``AssertionError`` in ``_finish_replay_locked``."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    await session.append_message(_user("kept"))
    fs.arm(lambda _c: "", NotAttempted(errno.EACCES, "refused (injected)"))
    with pytest.raises(SessionError) as exc:
        await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")
    assert exc.value.code == "storage"

    assert [_text(e) for e in await session.get_branch()] == ["seed", "the turn", "kept"]
    await session.append_message(_user("after"))
    await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")
    await session.move_to(await session.get_leaf_id())

    expected = ["seed", "kept", "after"]
    assert [_text(e) for e in await session.get_branch()] == expected
    assert await _reopened_branch(path) == expected
    assert fs.reads == 0, "nothing was pending"


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (OSError(errno.EIO, "landed then raised (injected)"), SessionError),
        (asyncio.CancelledError("injected"), asyncio.CancelledError),
    ],
    ids=["eio", "cancel"],
)
async def test_a_survivor_that_landed_and_raised_is_not_written_twice_and_the_rest_follow(
    tmp_path: Path, error: BaseException, raised: type[BaseException]
) -> None:
    """The first survivor's copy lands and raises. On ``ed76ea76`` the second
    survivor was never written: ``[seed, n1, after]``. Now the next write takes
    the landed copy in and appends the rest, once each."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    await session.append_message(_user("n1"))
    await session.append_message(_user("n2"))
    fs.arm(_whole, error, skip=1)  # the leaf move goes through; n1's copy does not
    with pytest.raises(raised):
        await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")

    await session.append_message(_user("after"))

    expected = ["seed", "n1", "n2", "after"]
    assert [_text(e) for e in await session.get_branch()] == expected
    assert await _reopened_branch(path) == expected
    in_file = await _texts_in_file(path)
    assert (in_file.count("n1"), in_file.count("n2")) == (2, 2), "one copy each"


async def test_a_survivor_that_was_not_attempted_is_written_by_the_next_write(
    tmp_path: Path,
) -> None:
    """``NotAttempted`` on a survivor: nothing of it was written and nothing is
    pending, but the withdrawal is owed. On ``ed76ea76``: ``[seed, after]``."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    await session.append_message(_user("n1"))
    fs.arm(lambda _c: "", NotAttempted(errno.EACCES, "refused (injected)"), skip=1)
    with pytest.raises(SessionError) as exc:
        await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")
    assert exc.value.code == "storage"

    await session.append_message(_user("after"))

    assert await _reopened_branch(path) == ["seed", "n1", "after"]
    assert fs.reads == 0, "nothing was pending"


async def test_a_reader_of_the_leaf_finishes_an_owed_withdrawal(tmp_path: Path) -> None:
    """A withdrawal cut part-way is finished before the leaf is read, so the tip a
    reader takes is the withdrawal's last survivor, not its half-way point."""

    session, fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    await session.append_message(_user("n1"))
    fs.arm(_whole, _enospc(), skip=1)
    with pytest.raises(SessionError):
        await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")

    leaf = await session.get_leaf_id()

    assert _text(await session.get_entry(leaf)) == "n1"  # type: ignore[arg-type]
    assert await _reopened_branch(path) == ["seed", "n1"]


async def test_a_survivor_that_can_no_longer_be_encoded_is_skipped_not_owed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A survivor whose payload its caller turned into something JSON cannot hold
    will never be written: it is logged and skipped, and the rest still follow,
    rather than refusing every write after it."""

    session, _fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("the turn"))
    data: dict[str, Any] = {"k": 1}
    await session.append_custom_entry("x.neighbour", data)
    await session.append_message(_user("n2"))
    data["k"] = {1, 2}  # the cached entry is the caller's object

    with caplog.at_level("WARNING"):
        await session.withdraw_tail(seed, lambda e: _text(e) == "the turn")

    assert "cannot be written again" in caplog.text
    assert await _reopened_branch(path) == ["seed", "n2"]
    after = await session.append_message(_user("after"))
    assert (await session.get_entry(after)).parent_id is not None  # type: ignore[union-attr]


async def test_an_append_that_comes_in_during_a_withdrawal_stays_on_the_branch(
    tmp_path: Path,
) -> None:
    """Codex's ``withdraw-concurrent``: a withdrawal and an append queued behind the
    store's lock, in that order. On ``ed76ea76`` the withdrawal read the path,
    the append landed, and the leaf move then left it behind:
    ``[seed, survivor]``."""

    session, _fs, path, seed = await _seeded(tmp_path)
    await session.append_message(_user("survivor"))
    storage = session.get_storage()
    await storage._lock.acquire()  # type: ignore[attr-defined]
    withdrawing = asyncio.create_task(session.withdraw_tail(seed, lambda _e: False))
    await asyncio.sleep(0)
    appending = asyncio.create_task(session.append_message(_user("concurrent")))
    await asyncio.sleep(0)
    storage._lock.release()  # type: ignore[attr-defined]
    await asyncio.gather(withdrawing, appending)

    assert await _reopened_branch(path) == ["seed", "survivor", "concurrent"]


# === what is adopted is what the file holds (Codex review of #721, finding 2) =====


async def test_a_pending_entry_its_caller_changed_is_adopted_as_the_file_holds_it(
    tmp_path: Path,
) -> None:
    """The line lands and raises, then the caller changes the payload it still
    holds. On ``ed76ea76`` memory took the changed object (``{"k": 2}``) while the
    file held ``{"k": 1}``. Now memory holds what a reload would."""

    session, fs, path, _seed = await _seeded(tmp_path)
    data = {"k": 1}
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_custom_entry("x.record", data)
    data["k"] = 2

    await session.append_message(_user("after"))

    record = next(e for e in await session.get_entries() if e.type == "custom")
    assert record.data == {"k": 1}  # type: ignore[union-attr]
    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert [e.id for e in await reopened.get_entries()] == [e.id for e in await session.get_entries()]


async def test_an_unchanged_pending_entry_is_adopted_as_the_callers_own_object(
    tmp_path: Path,
) -> None:
    """The harness tells its messages apart by identity (Esc-withdraw's
    ``is_withdrawn``), so a landed line whose entry still encodes to it is taken in
    as the very object the caller built."""

    session, fs, _path, _seed = await _seeded(tmp_path)
    storage = session.get_storage()
    built: list[Any] = []

    def build(entry_id: str, parent_id: str | None) -> Any:
        entry = CustomMessageEntry(
            id=entry_id, parent_id=parent_id, timestamp="2026-11-08T00:00:00.000Z",
            custom_type="x", content="body", display=True, details={"k": 1},
        )
        built.append(entry)
        return entry

    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await storage.append_to_leaf(build)

    await storage.resolve_pending()

    assert await storage.get_entry(built[0].id) is built[0]


async def test_a_pending_entry_changed_into_what_json_cannot_hold_is_adopted_from_the_file(
    tmp_path: Path,
) -> None:
    """The caller turned the payload into something that no longer encodes (a
    ``set``) after its line landed and raised: that object cannot be the line, so
    memory takes the parsed one. Counted as matching (the verify round's mutant
    R9), memory held the ``set`` a reload never sees."""

    session, fs, path, _seed = await _seeded(tmp_path)
    details: dict[str, Any] = {"k": 1}
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_custom_message_entry("x.notice", "body", True, details)
    details["k"] = {1, 2}

    await session.append_message(_user("after"))

    notice = next(e for e in await session.get_entries() if e.type == "custom_message")
    assert notice.details == {"k": 1}  # type: ignore[union-attr]
    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert (await reopened.get_entry(notice.id)).details == {"k": 1}  # type: ignore[union-attr]


async def test_a_placement_that_landed_and_raised_is_adopted_as_a_copy(tmp_path: Path) -> None:
    """A placement is always taken from the file, settled or not: the built object
    stays its caller's to change. Adopted as the built object (the verify round's
    mutant R10), a change the caller made after the settle reached memory."""

    session, fs, path, _seed = await _seeded(tmp_path)
    storage = session.get_storage()
    built: list[Any] = []

    def build(parent_id: str | None) -> Any:
        entry = CustomMessageEntry(
            id="nt-ffffffffffff", parent_id=parent_id, timestamp="2026-11-08T00:00:00.000Z",
            custom_type="x", content="body", display=True, details={"k": 1},
        )
        built.append(entry)
        return entry

    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await storage.place_entry("nt-ffffffffffff", build, same=lambda _e: True)
    await storage.resolve_pending()
    built[0].details["k"] = 2

    stored = await storage.get_entry("nt-ffffffffffff")
    assert stored is not built[0]
    assert stored.details == {"k": 1}  # type: ignore[union-attr]
    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert (await reopened.get_entry("nt-ffffffffffff")).details == {"k": 1}  # type: ignore[union-attr]


async def test_a_reused_id_whose_line_landed_is_taken_in_as_the_newer_line(
    tmp_path: Path,
) -> None:
    """``append_entry`` with an id already stored lands its line and raises, and the
    caller then changes the payload. The settle takes the entry from the file, and
    it is the NEWER line, as the loader resolves the id. Taking the first line
    with the id (the verify round's mutant R2) put the older payload in memory."""

    session, fs, path, seed = await _seeded(tmp_path)
    storage = session.get_storage()
    stamp = "2026-11-08T00:00:00.000Z"
    await storage.append_entry(
        CustomEntry(id="dup", parent_id=seed, timestamp=stamp, custom_type="x.r", data={"v": 1})
    )
    data = {"v": 2}
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await storage.append_entry(
            CustomEntry(id="dup", parent_id=seed, timestamp=stamp, custom_type="x.r", data=data)
        )
    data["v"] = 3

    await storage.resolve_pending()

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert (await reopened.get_entry("dup")).data == {"v": 2}  # type: ignore[union-attr]
    assert (await storage.get_entry("dup")).data == {"v": 2}  # type: ignore[union-attr]


async def test_an_append_that_reused_a_stored_id_and_did_not_land_is_not_adopted(
    tmp_path: Path,
) -> None:
    """The verify round's ``dupid.py``: ``append_entry`` with an id already stored,
    half a line lands. On ``ed76ea76`` the store found the id in the file — the
    OLD line's — and adopted the new entry: memory gained an entry a reload does
    not have (``same: false``). The line written is matched now, not the id."""

    from dataclasses import replace

    session, fs, path, _seed = await _seeded(tmp_path)
    storage = session.get_storage()
    first = await session.append_message(_user("a"))
    second = await session.append_message(_user("b"))
    entry = await storage.get_entry(first)
    fs.arm(_half, OSError(errno.EIO, "partial (injected)"))
    with pytest.raises(SessionError):
        await storage.append_entry(replace(entry, parent_id=second))  # type: ignore[type-var]

    await session.append_message(_user("c"))

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    memory = [(e.id, e.parent_id) for e in await storage.get_entries()]
    assert memory == [(e.id, e.parent_id) for e in await reopened.get_entries()]
    assert await storage.get_leaf_id() == await reopened.get_leaf_id()


# === readers take the settled leaf (Codex review of #721, finding 5) ===============


@pytest.mark.parametrize("first", ["get_leaf_id", "get_branch"])
async def test_the_leaf_a_reader_takes_includes_a_line_that_landed_and_raised(
    tmp_path: Path, first: str
) -> None:
    """``Session.get_leaf_id`` and ``get_branch`` each settle first. On
    ``ed76ea76`` they answered from before the line, which is how ``/clone``
    copied a branch without its final entry (Codex's ``rpc-clone-stale-tip``)."""

    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed answer"))

    if first == "get_branch":
        assert [_text(e) for e in await session.get_branch()] == ["seed", "landed answer"]
    leaf = await session.get_leaf_id()

    assert leaf != seed and _text(await session.get_entry(leaf)) == "landed answer"  # type: ignore[arg-type]
    assert [_text(e) for e in await session.get_branch()] == ["seed", "landed answer"]
    assert fs.reads == 1
    assert await _reopened_branch(path) == ["seed", "landed answer"]


async def test_esc_withdraw_after_a_landed_and_raised_answer_writes_no_copy_of_it(
    tmp_path: Path,
) -> None:
    """The verify round's ``harness_safe_leaf.py``: prompt 1's answer lands and
    raises, prompt 2 is withdrawn with Esc. The point it withdraws to is read
    settled now, so the answer is not copied: on ``ed76ea76`` the file held
    ``a1`` twice (``[seed, q1, a1, q2, leaf, a1]``)."""

    session, fs, path, _seed = await _seeded(tmp_path)
    mode = {"hang": False}
    started = asyncio.Event()

    async def stream(*_args: Any, **_kwargs: Any) -> Any:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if mode["hang"]:
            started.set()
            await asyncio.Event().wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="a1")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream, session=session))
    fs.arm(_whole, _enospc(), skip=1)  # q1's line goes through; a1's lands and raises
    await harness.prompt("q1")
    mode["hang"] = True
    turn = asyncio.create_task(harness.prompt("q2"))
    await started.wait()
    await harness.abort(withdraw_unanswered=True)
    await turn

    assert await _reopened_branch(path) == ["seed", "q1", "a1"]
    assert (await _texts_in_file(path)).count("a1") == 1


# === move_to and set_leaf_id settle before they validate (verify should-fix 2) =====


async def test_move_to_finds_a_target_read_from_the_file_before_anything_else_settles(
    tmp_path: Path,
) -> None:
    """The target is the landed line's id, read from the file, and the move is the
    first thing after the failure — so only ``move_to``'s own settling can find
    it (the verify round's mutant V9 removed it and every test still passed)."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    landed = next(
        e.id
        for e in await (await JsonlSessionStorage.open(LocalFileSystem(), str(path))).get_entries()
        if _text(e) == "landed"
    )

    await session.move_to(landed)

    assert await session.get_storage().get_leaf_id() == landed


async def test_set_leaf_id_finds_a_target_read_from_the_file(tmp_path: Path) -> None:
    """The same through the store itself (``harness/core.py``'s pending-write
    drain calls ``set_leaf_id`` directly): it settles, then validates (mutant V10
    validated first and raised ``not_found``)."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    landed = next(
        e.id
        for e in await (await JsonlSessionStorage.open(LocalFileSystem(), str(path))).get_entries()
        if _text(e) == "landed"
    )

    await session.get_storage().set_leaf_id(landed)

    assert await _reopened_branch(path) == ["seed", "landed"]


# === place_entry ==================================================================


async def test_place_entry_is_idempotent_and_rejects_a_different_payload(tmp_path: Path) -> None:
    """On main there is no explicit-id placement at all, and ``append_entry`` with a
    repeated id overwrote ``_by_id`` (Codex r5 finding 3, second half)."""

    session, _fs, path, seed = await _seeded(tmp_path)

    first = await session.place_custom_message(
        "nt-0123456789ab", "aelix.job_notice", "body", True, {"v": 1}
    )
    again = await session.place_custom_message(
        "nt-0123456789ab", "aelix.job_notice", "body", True, {"v": 1}
    )
    with pytest.raises(PlacementConflict) as exc:
        await session.place_custom_message(
            "nt-0123456789ab", "aelix.job_notice", "OTHER", True, {"v": 1}
        )

    assert (first.adopted, again.adopted) == (False, True)
    assert again.entry is first.entry
    assert first.entry.parent_id == seed
    assert exc.value.code == "invalid_entry" and exc.value.entry_id == "nt-0123456789ab"
    stored = await session.get_entry("nt-0123456789ab")
    assert isinstance(stored, CustomMessageEntry) and stored.content == "body"
    lines = path.read_text().splitlines()
    assert sum('"nt-0123456789ab"' in line for line in lines) == 1, "written once"


async def test_a_placement_whose_outcome_was_unknown_is_placed_again_without_a_duplicate(
    tmp_path: Path,
) -> None:
    """The notice path S1b relies on: ``outcome_unknown``, then the same placement."""

    session, fs, path, _seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError) as exc:
        await session.place_custom_message("nt-aaaaaaaaaaaa", "aelix.job_notice", "body", True)
    assert exc.value.code == "outcome_unknown"

    placed = await session.place_custom_message("nt-aaaaaaaaaaaa", "aelix.job_notice", "body", True)

    assert placed.adopted is True
    assert sum('"nt-aaaaaaaaaaaa"' in line for line in path.read_text().splitlines()) == 1
    assert (await _reopened_branch(path))[-1] == "body"


async def test_place_entry_with_a_custom_same_compares_only_what_it_names(tmp_path: Path) -> None:
    session, _fs, _path, _seed = await _seeded(tmp_path)
    await session.place_custom_message(
        "nt-bbbbbbbbbbbb", "aelix.job_notice", "body", True, {"notice_id": "j:1"}
    )

    def same_notice(existing: Any) -> bool:
        return (getattr(existing, "details", None) or {}).get("notice_id") == "j:1"

    adopted = await session.place_custom_message(
        "nt-bbbbbbbbbbbb",
        "aelix.job_notice",
        "re-rendered",
        True,
        {"notice_id": "j:1"},
        same=same_notice,
    )
    assert adopted.adopted is True and adopted.entry.content == "body"  # type: ignore[union-attr]


@pytest.mark.parametrize("lands", ["landed-then-raised", "written"])
async def test_a_placement_compares_against_what_was_written_not_the_callers_dict(
    tmp_path: Path, lands: str
) -> None:
    """Codex's ``pending-payload-changed`` and ``successful-payload-changed``: the
    caller places ``{"value": 1}``, then changes that dict to ``{"value": 2}`` and
    places ``{"value": 2}``. On ``ed76ea76`` the store held the caller's dict, so
    the retry was adopted, memory read ``{"value": 2}``, the file ``{"value": 1}``
    — and the same retry after a reopen was a conflict. Now it is a conflict in
    this process too, and memory holds what the file holds."""

    session, fs, path, _seed = await _seeded(tmp_path)
    details = {"value": 1}
    if lands == "landed-then-raised":
        fs.arm(_whole, OSError(errno.EIO, "after newline (injected)"))
        with pytest.raises(SessionError):
            await session.place_custom_message("fixed", "x", "body", True, details)
    else:
        await session.place_custom_message("fixed", "x", "body", True, details)
    details["value"] = 2

    with pytest.raises(PlacementConflict):
        await session.place_custom_message("fixed", "x", "body", True, {"value": 2})

    assert (await session.get_entry("fixed")).details == {"value": 1}  # type: ignore[union-attr]
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    assert (await reopened.get_entry("fixed")).details == {"value": 1}  # type: ignore[union-attr]
    adopted = await session.place_custom_message("fixed", "x", "body", True, {"value": 1})
    assert adopted.adopted is True


async def test_a_same_payload_retry_that_json_cannot_round_trip_is_a_conflict(
    tmp_path: Path,
) -> None:
    """A limit, pinned: ``same`` compares against the line parsed back, so a payload
    a JSON round trip changes — a tuple comes back a list — does not match its own
    retry. ``PlacementConflict`` in this process, as after a reopen. No product
    caller places such a payload; keeping payloads to JSON values is the
    caller's job (ADR-0242 rule 3)."""

    session, _fs, _path, _seed = await _seeded(tmp_path)
    await session.place_custom_message("nt-cccccccccccc", "x", "body", True, {"pair": (1, 2)})

    with pytest.raises(PlacementConflict):
        await session.place_custom_message("nt-cccccccccccc", "x", "body", True, {"pair": (1, 2)})
    assert (await session.get_entry("nt-cccccccccccc")).details == {"pair": [1, 2]}  # type: ignore[union-attr]


async def test_a_placement_returns_the_entry_it_stored_not_the_one_it_built(
    tmp_path: Path,
) -> None:
    session, _fs, _path, _seed = await _seeded(tmp_path)
    storage = session.get_storage()
    built: list[Any] = []

    def build(parent_id: str | None) -> Any:
        entry = CustomMessageEntry(
            id="nt-dddddddddddd", parent_id=parent_id, timestamp="2026-11-08T00:00:00.000Z",
            custom_type="x", content="body", display=True, details={"k": [1]},
        )
        built.append(entry)
        return entry

    placed = await storage.place_entry("nt-dddddddddddd", build, same=lambda _e: True)

    assert placed.entry is not built[0] and placed.entry == built[0]
    assert placed.entry.details is not built[0].details  # type: ignore[union-attr]
    assert await storage.get_entry("nt-dddddddddddd") is placed.entry


async def test_a_placement_its_own_loader_would_skip_is_kept_as_built(tmp_path: Path) -> None:
    """A ``build`` that leaves the timestamp empty writes a line the loader skips;
    the store keeps the entry as built rather than raising after the write."""

    session, _fs, _path, _seed = await _seeded(tmp_path)
    storage = session.get_storage()
    entry = CustomMessageEntry(
        id="nt-eeeeeeeeeeee", parent_id=None, timestamp="",
        custom_type="x", content="body", display=True,
    )

    placed = await storage.place_entry("nt-eeeeeeeeeeee", lambda _p: entry, same=lambda _e: True)

    assert placed.entry is entry and placed.adopted is False


# === finding 4: the failure classes ===============================================


@pytest.mark.parametrize(
    "interrupt",
    [asyncio.CancelledError(), KeyboardInterrupt()],
    ids=["cancelled", "keyboard-interrupt"],
)
async def test_cancelled_after_bytes_reraises_unchanged_and_next_write_resolves(
    tmp_path: Path, interrupt: BaseException
) -> None:
    """A cancel that lands after the line is written is still a cancel — not a
    ``SessionError`` a caller would log and carry on from — and the line it left
    is taken in by the next write."""

    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, interrupt)
    with pytest.raises(type(interrupt)):
        await session.append_message(_user("landed"))

    after = await session.append_message(_user("after"))

    assert fs.reads == 1, "the next write read the file back once"
    parent = await session.get_entry((await session.get_entry(after)).parent_id)  # type: ignore[arg-type,union-attr]
    assert _text(parent) == "landed" and parent.parent_id == seed  # type: ignore[union-attr]
    assert await _reopened_branch(path) == ["seed", "landed", "after"]


async def test_not_attempted_is_retryable_without_pending(tmp_path: Path) -> None:
    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(lambda _content: "", NotAttempted(errno.EACCES, "open refused (injected)"))
    with pytest.raises(SessionError) as exc:
        await session.append_message(_user("retry me"))
    assert exc.value.code == "storage"
    assert isinstance(exc.value.__cause__, NotAttempted)

    retried = await session.append_message(_user("retry me"))

    assert fs.reads == 0, "nothing was pending, so nothing was read back"
    assert (await session.get_entry(retried)).parent_id == seed  # type: ignore[union-attr]
    raw = path.read_text()
    assert raw.count("retry me") == 1 and "\n\n" not in raw, "the heal flag was left as it was"
    assert await _reopened_branch(path) == ["seed", "retry me"]


async def test_invalid_entry_stays_non_retryable(tmp_path: Path) -> None:
    """An entry ``json.dumps`` cannot encode is refused before a byte is written,
    as ADR-0242 rule 1.7 has it — and leaves nothing to settle."""

    session, fs, path, seed = await _seeded(tmp_path)
    before = path.read_bytes()
    with pytest.raises(SessionError) as exc:
        await session.append_custom_entry("x.bad", {"a set": {1, 2}})
    assert exc.value.code == "invalid_entry"
    assert path.read_bytes() == before

    after = await session.append_message(_user("after"))

    assert fs.reads == 0
    assert (await session.get_entry(after)).parent_id == seed  # type: ignore[union-attr]


async def test_unreadable_file_freezes_the_leaf(tmp_path: Path) -> None:
    """The read-back fails: the write that asked is refused with nothing written,
    and the leaf stays put until a read succeeds."""

    session, fs, path, seed = await _seeded(tmp_path)
    fs.arm(_whole, _enospc())
    with pytest.raises(SessionError):
        await session.append_message(_user("landed"))
    fs.fail_reads = True
    frozen = path.read_bytes()

    for attempt in range(2):
        with pytest.raises(SessionError) as exc:
            await session.append_message(_user(f"blocked {attempt}"))
        assert exc.value.code == "outcome_unknown"
        assert "unresolved" in str(exc.value)
        assert path.read_bytes() == frozen
        assert await session.get_leaf_id() == seed
    with pytest.raises(SessionError):
        await session.get_storage().set_leaf_id(seed)
    assert path.read_bytes() == frozen

    fs.fail_reads = False
    after = await session.append_message(_user("after"))

    assert _text(await session.get_entry((await session.get_entry(after)).parent_id)) == "landed"  # type: ignore[arg-type,union-attr]
    assert await _reopened_branch(path) == ["seed", "landed", "after"]


async def test_local_filesystem_says_not_attempted_only_before_the_first_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LocalFileSystem.append_file``: a refused ``open`` or a failing first
    ``os.write`` is :class:`NotAttempted`; a failure after a byte is plain ``OSError``."""

    import os

    target = tmp_path / "f.jsonl"

    def refuse_open(*_a: Any, **_k: Any) -> int:
        raise PermissionError(errno.EACCES, "Permission denied (injected)", str(target))

    monkeypatch.setattr(fs_mod, "os", _OsProxy(open=refuse_open))
    with pytest.raises(NotAttempted) as opened:
        await LocalFileSystem().append_file(str(target), "{}\n")
    assert opened.value.errno == errno.EACCES and opened.value.filename == str(target)

    def first_write_fails(_fd: int, _data: Any) -> int:
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    monkeypatch.setattr(fs_mod, "os", _OsProxy(write=first_write_fails))
    with pytest.raises(NotAttempted) as first:
        await LocalFileSystem().append_file(str(target), "{}\n")
    assert first.value.errno == errno.ENOSPC

    calls = {"n": 0}

    def second_write_fails(fd: int, data: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return os.write(fd, bytes(data[:1]))
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    monkeypatch.setattr(fs_mod, "os", _OsProxy(write=second_write_fails))
    with pytest.raises(OSError) as later:
        await LocalFileSystem().append_file(str(target), "{}\n")
    assert not isinstance(later.value, NotAttempted)
    assert target.read_bytes().endswith(b"{")


# === the other two backends =======================================================


async def test_memory_storage_never_has_a_pending_entry() -> None:
    """A memory write either changed memory or raised before it did: an append
    that lands and then raises is simply there, and the next entry follows it."""

    class _LandsThenRaises(MemorySessionStorage):
        armed = False

        async def append_entry(self, entry: Any) -> None:  # type: ignore[override]
            await super().append_entry(entry)
            if self.armed:
                self.armed = False
                raise OSError(errno.ENOSPC, "injected")

    storage = _LandsThenRaises()
    session = Session(storage)
    await session.append_message(_user("seed"))
    storage.armed = True
    with pytest.raises(OSError):
        await session.append_message(_user("landed"))

    await storage.resolve_pending()
    after = await session.append_message(_user("after"))

    assert [_text(e) for e in await session.get_branch()] == ["seed", "landed", "after"]
    assert (await session.get_entry(after)) is not None


async def test_the_read_only_store_refuses_the_new_writes(tmp_path: Path) -> None:
    session, _fs, path, seed = await _seeded(tmp_path)
    viewer = Session(
        ReadOnlySessionStorage(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    )
    before = path.read_bytes()

    await viewer.get_storage().resolve_pending()
    with pytest.raises(SessionError) as appended:
        await viewer.append_message(_user("no"))
    with pytest.raises(SessionError) as placed:
        await viewer.place_custom_message("nt-cccccccccccc", "aelix.job_notice", "no", True)
    with pytest.raises(SessionError) as withdrawn:
        await viewer.withdraw_tail(None, lambda _e: True)

    assert (appended.value.code, placed.value.code, withdrawn.value.code) == ("read_only",) * 3
    assert path.read_bytes() == before
    assert await viewer.get_leaf_id() == seed


# === through the harness ==========================================================


async def test_a_turn_whose_user_line_landed_and_raised_still_answers_on_that_line(
    tmp_path: Path,
) -> None:
    """The product path: the harness logs a failed ``message_end`` append at DEBUG
    and goes on (ADR-0242 §0 [3]), so the assistant's entry is the next write. On
    main it was parented on the seed and the user's line left the branch."""

    session, fs, path, _seed = await _seeded(tmp_path)

    async def stream(*_args: Any, **_kwargs: Any) -> Any:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="answer")], stop_reason="end_turn")
        )

    harness = AgentHarness(AgentHarnessOptions(stream_fn=stream, session=session))
    fs.arm(_whole, _enospc())  # the user message's line
    await harness.prompt("question")

    assert await _reopened_branch(path) == ["seed", "question", "answer"]
