"""``JsonlSessionStorage`` (Sprint 4a / Phase 2.2.1).

Pi source: ``packages/agent/src/harness/session/jsonl-storage.ts`` —
header version 3 + leaf-tracking + label cache. Aelix port keeps the
on-disk format **identical** (header v3 with camelCase keys) so a Pi
session file round-trips through Aelix and back.

Aelix-additive (per ADR-0022): per-instance ``asyncio.Lock`` around
appends. Pi has no lock; the Aelix lock is a strict superset safety net
that lets concurrent ``append_*`` calls serialize cleanly under asyncio.

The write contract (ADR-0242, #294). Every storage call that writes —
``append_entry``, ``set_leaf_id`` — appends exactly one ``\\n``-terminated
JSON object with exactly one ``FileSystem.append_file`` call, so the file can
end after any line and every such prefix is itself a valid session. A whole
file (``create``, and a fork built through it) is published atomically:
staged in ``<path>.tmp`` and renamed into place, so it appears whole or not
at all. A write that fails part-way re-arms the healing newline, so it never
costs the entry appended after it.

A write that raised after its ``append_file`` call began has an unknown
outcome: the line may be whole on disk, cut, or absent. The store keeps that
entry pending and settles it under its lock, by reading the file back, before
it writes again — so the next entry's parent is chosen from what the file
holds, not from a cache that never saw the line (#721, ADR-0242 rule 5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from aelix_agent_core.session.entries import (
    LeafEntry,
    SessionTreeEntry,
    entry_from_json,
    entry_to_json,
)
from aelix_agent_core.session.fs import FileSystem, NotAttempted
from aelix_agent_core.session.storage import (
    JsonlSessionMetadata,
    Placement,
    PlacementConflict,
    SessionError,
    SessionStorage,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRecovery:
    """What was dropped to make a damaged session file loadable.

    Emitted by :func:`_load_jsonl_storage` when a session JSONL contains
    lines it could not parse — most often a single crash-truncated final
    line, because the process died mid-append. ``None`` (not an empty
    instance) is returned for clean files, so ``if recovery is not None``
    is the "this file was damaged" test.
    """

    file_path: str
    skipped_lines: tuple[int, ...]
    orphaned_entries: tuple[str, ...]
    #: The file does not end in a newline, i.e. the last write was cut
    #: off mid-line. The next append has to open a fresh line or it
    #: would be glued onto the fragment and lost with it.
    unterminated_tail: bool = False

    def describe(self) -> str:
        lines = ", ".join(str(n) for n in self.skipped_lines)
        msg = (
            f"Recovered damaged session file {self.file_path}: skipped "
            f"{len(self.skipped_lines)} unparseable line(s) [{lines}]"
        )
        if self.orphaned_entries:
            msg += (
                f" and dropped {len(self.orphaned_entries)} entry/entries "
                f"orphaned by the gap"
            )
        return msg


@dataclass(frozen=True)
class _LoadedSession:
    """Everything :func:`_load_jsonl_storage` learned about a file.

    ``ends_with_newline`` is deliberately independent of ``recovery``: a
    file can be perfectly valid and still lack its final newline, and
    that alone decides whether the next append needs a healing newline.
    """

    header: _SessionHeader
    entries: list[SessionTreeEntry]
    leaf_id: str | None
    recovery: SessionRecovery | None
    ends_with_newline: bool


def _iso_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _encode_line(payload: dict[str, Any], what: str) -> str:
    """``payload`` as one ``\\n``-terminated JSON line, or ``invalid_entry``.

    ``json.dumps`` escapes every control character inside a string (a
    newline in ``data`` is written as the two characters ``\\`` ``n``) and,
    with its default ``ensure_ascii``, every non-ASCII one, so the
    terminator is the only raw newline in the line. It raises on what it
    cannot encode — a dataclass, a ``set``, ``bytes``, a circular reference
    — and that has to surface before a single byte reaches the file
    (ADR-0242). What it can encode it writes its own way, unchecked: a tuple
    as a list, a non-string key as a string, ``NaN`` bare. Those come back
    changed on reload, and keeping payloads to JSON values is the caller's
    job.
    """

    try:
        return json.dumps(payload) + "\n"
    except (TypeError, ValueError) as exc:
        raise SessionError(
            "invalid_entry",
            f"Cannot persist session {what}: not a JSON value ({exc})",
            cause=exc,
        ) from exc


def _encode_entry(entry: SessionTreeEntry, what: str) -> str:
    """``entry`` as one line, or ``invalid_entry`` from either conversion.

    :func:`entry_to_json` can fail before ``json.dumps`` is reached: a
    ``message`` that is not a dataclass makes ``asdict`` raise ``TypeError``.
    Nothing has been written at that point either, so it is the same refusal
    as a payload ``json.dumps`` cannot encode rather than a raw ``TypeError``.
    """

    try:
        payload = entry_to_json(entry)
    except (TypeError, ValueError) as exc:
        raise SessionError(
            "invalid_entry",
            f"Cannot persist session {what}: not a JSON value ({exc})",
            cause=exc,
        ) from exc
    return _encode_line(payload, what)


def _encodes_to(entry: SessionTreeEntry, line: str) -> bool:
    """Does ``entry`` still encode to exactly ``line``? ``False`` if it no longer encodes."""

    try:
        return _encode_entry(entry, f"entry {entry.id}") == line
    except SessionError:
        return False


@dataclass(frozen=True)
class _Pending:
    """A write whose outcome is unknown: the entry, its line, and how to adopt it (#721)."""

    entry: SessionTreeEntry
    line: str
    snapshot: bool


@dataclass
class _Replay:
    """What an interrupted :meth:`JsonlSessionStorage.withdraw_tail` still has to write.

    ``moved`` is whether its leaf move is known to be in the file. Until it is,
    nothing has happened, and a move that turns out not to have landed ends
    the withdrawal there. After it, ``survivors`` are owed until written.
    """

    survivors: list[SessionTreeEntry]
    moved: bool


async def _publish_file(fs: FileSystem, path: str, content: str) -> None:
    """Make ``path`` appear holding exactly ``content``, or not at all.

    Pi ``publishFileAtomically`` (``jsonl/io.ts``): stage ``<path>.tmp``,
    then rename it over ``path`` (ADR-0242). Creating the file directly and
    appending to it — what ``create`` plus a per-entry ``append_entry`` loop
    did for every fork — left a truncated session behind when a write failed
    part-way, and ``find_most_recent`` then resumed it (#294).

    The temp name does not end in ``.jsonl``, so ``list`` and
    ``find_most_recent`` never see it. On any failure it is removed best
    effort; a crash between staging and the rename can still leave it
    behind (owner-only, never listed, overwritten by the next publish to the
    same path — nothing sweeps it). An exception does not prove the rename
    did not happen: an interrupt can land just after it, and then ``path``
    holds the whole ``content``. That file is never rolled back.
    """

    temp = f"{path}.tmp"
    try:
        await fs.write_file(temp, content)
        await fs.rename_file(temp, path)
    except BaseException as exc:
        with contextlib.suppress(OSError):
            await fs.remove(temp, force=True)
        if isinstance(exc, OSError):
            raise SessionError(
                "storage",
                f"Failed to create session {path}: {exc}",
                cause=exc,
            ) from exc
        raise


def _generate_entry_id(by_id: dict[str, SessionTreeEntry]) -> str:
    """Pi ``generateEntryId`` (``jsonl-storage.ts:35-41``)."""

    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in by_id:
            return candidate
    return uuid.uuid4().hex


def _leaf_id_after_entry(entry: SessionTreeEntry) -> str | None:
    """Pi ``leafIdAfterEntry`` (``jsonl-storage.ts:109-111``)."""

    if entry.type == "leaf":
        return entry.target_id  # type: ignore[union-attr]
    return entry.id


def _update_label_cache(
    labels_by_id: dict[str, str], entry: SessionTreeEntry
) -> None:
    """Pi ``updateLabelCache`` (``jsonl-storage.ts:17-25``)."""

    if entry.type != "label":
        return
    raw = entry.label  # type: ignore[union-attr]
    stripped = raw.strip() if raw else None
    if stripped:
        labels_by_id[entry.target_id] = stripped  # type: ignore[union-attr]
    else:
        labels_by_id.pop(entry.target_id, None)  # type: ignore[union-attr]


@dataclass(frozen=True)
class _SessionHeader:
    """Pi ``SessionHeader`` (``jsonl-storage.ts:8-15``)."""

    id: str
    timestamp: str
    cwd: str
    parent_session: str | None = None
    version: int = 3


def _header_to_metadata(
    header: _SessionHeader, path: str
) -> JsonlSessionMetadata:
    return JsonlSessionMetadata(
        id=header.id,
        created_at=header.timestamp,
        cwd=header.cwd,
        path=path,
        parent_session_path=header.parent_session,
    )


def _parse_header_line(line: str, file_path: str) -> _SessionHeader:
    """Pi ``parseHeaderLine`` (``jsonl-storage.ts:59-85``)."""

    try:
        parsed = json.loads(line)
    except Exception as exc:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc
    if not isinstance(parsed, dict):
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
        )
    if parsed.get("type") != "session":
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
        )
    if parsed.get("version") != 3:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: unsupported session "
            "version",
        )
    sid = parsed.get("id")
    if not isinstance(sid, str) or not sid:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing id",
        )
    ts = parsed.get("timestamp")
    if not isinstance(ts, str) or not ts:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing timestamp",
        )
    cwd = parsed.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing cwd",
        )
    parent = parsed.get("parentSession")
    if parent is not None and not isinstance(parent, str):
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header "
            "parentSession must be a string",
        )
    return _SessionHeader(
        id=sid, timestamp=ts, cwd=cwd, parent_session=parent, version=3
    )


def _parse_entry_line(
    line: str, file_path: str, line_number: int
) -> SessionTreeEntry:
    """Pi ``parseEntryLine`` (``jsonl-storage.ts:87-107``)."""

    try:
        parsed = json.loads(line)
    except Exception as exc:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is not valid JSON",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc
    if not isinstance(parsed, dict):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is not a valid session entry",
        )
    if not isinstance(parsed.get("type"), str):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing entry type",
        )
    if not isinstance(parsed.get("id"), str) or not parsed["id"]:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing entry id",
        )
    parent_id = parsed.get("parentId")
    if parent_id is not None and not isinstance(parent_id, str):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "has invalid parentId",
        )
    if not isinstance(parsed.get("timestamp"), str) or not parsed["timestamp"]:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing timestamp",
        )
    if (
        parsed["type"] == "session_info"
        and parsed.get("name") is not None
        and not isinstance(parsed["name"], str)
    ):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "has invalid session name",
        )
    if parsed["type"] == "leaf":
        target = parsed.get("targetId")
        if target is not None and not isinstance(target, str):
            raise SessionError(
                "invalid_entry",
                f"Invalid JSONL session file {file_path}: line "
                f"{line_number} has invalid targetId",
            )
    try:
        return entry_from_json(parsed)
    except Exception as exc:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            f"failed to decode: {exc}",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc


async def load_jsonl_session_metadata(
    fs: FileSystem, file_path: str
) -> JsonlSessionMetadata:
    """Pi ``loadJsonlSessionMetadata`` (``jsonl-storage.ts:123-134``)."""

    try:
        lines = await fs.read_text_lines(file_path, max_lines=1)
    except OSError as exc:
        raise SessionError(
            "storage",
            f"Failed to read session header {file_path}: {exc}",
            cause=exc,
        ) from exc
    if not lines or not lines[0].strip():
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: missing session header",
        )
    header = _parse_header_line(lines[0], file_path)
    return _header_to_metadata(header, file_path)


async def _load_jsonl_storage(
    fs: FileSystem, file_path: str
) -> _LoadedSession:
    """Pi ``loadJsonlStorage`` (``jsonl-storage.ts:136-159``).

    Aelix-additive divergence (Track S1): entry lines that fail to parse
    are **skipped**, not fatal. Pi (and Aelix before this change) raised
    on the first bad line, which bricked the file permanently — a crash
    mid-append truncates the LAST line, and since ``find_most_recent``
    only validates the HEADER it re-selected the same dead file on every
    subsequent ``--continue``, with no way out but deleting it by hand.

    The header check stays strict: a file whose first line is not a v3
    session header is genuinely "not a session file" and still raises
    ``invalid_session``. Only the entry body is recovered.

    Skipping an INTERIOR line can orphan later entries whose ``parentId``
    referred to the dropped one; those would blow up in
    :meth:`JsonlSessionStorage.get_path_to_root`. So when anything was
    skipped, entries with an unresolvable parent are pruned too
    (transitively), and the leaf pointer is rewound to the newest
    surviving entry. Clean files take none of this: they short-circuit
    to the original behaviour with ``recovery=None``.

    Recovery is non-destructive — the damaged bytes stay on disk and are
    re-skipped on every load. TODO (Track S, follow-up): an explicit
    ``aelix session repair <file>`` that rewrites the file without them.
    It is deferred because the CLI has no subcommand group yet, so it is
    a new surface rather than a fix; recover-on-load is what actually
    unbricks ``--continue``, and it lands here.
    """

    try:
        content = await fs.read_text_file(file_path)
    except OSError as exc:
        raise SessionError(
            "storage",
            f"Failed to read session {file_path}: {exc}",
            cause=exc,
        ) from exc
    loaded = _parse_jsonl_content(content, file_path)
    if loaded.recovery is not None:
        # Keep recovery warnings on actual open; picker inspection is read-only.
        _LOG.warning("%s", loaded.recovery.describe())
    return loaded


def _parse_jsonl_content(content: str, file_path: str) -> _LoadedSession:
    """Decode retained entries with the storage loader's recovery semantics.

    Shared internally with the resume-label reader (#417): a tail-only name
    lookup cannot see a corrupt ancestor that makes a later rename orphaned.
    No I/O, logging, or repair writes occur here.
    """

    lines = [line for line in content.split("\n") if line.strip()]
    if not lines:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: missing session header",
        )
    header = _parse_header_line(lines[0], file_path)
    # Whether the last write finished its line. Computed from the BYTES,
    # not from whether anything was skipped: a file can be entirely valid
    # and still be missing its final newline (a pi export, a hand-edit, or
    # `import_from_jsonl`, which copies foreign bytes verbatim). Appending
    # to such a file without healing it first fuses the new entry onto the
    # last one, and the NEXT load then drops the fused line — destroying a
    # turn that was already durable.
    ends_with_newline = content.endswith("\n")
    entries: list[SessionTreeEntry] = []
    leaf_id: str | None = None
    skipped: list[int] = []
    for i in range(1, len(lines)):
        try:
            entry = _parse_entry_line(lines[i], file_path, i + 1)
        except SessionError:
            skipped.append(i + 1)
            continue
        entries.append(entry)
        leaf_id = _leaf_id_after_entry(entry)
    if not skipped:
        return _LoadedSession(
            header=header,
            entries=entries,
            leaf_id=leaf_id,
            recovery=None,
            ends_with_newline=ends_with_newline,
        )

    # A gap was bridged — re-check that the surviving forest is still
    # internally consistent before handing it to the caller.
    kept: list[SessionTreeEntry] = []
    known: set[str] = set()
    orphaned: list[str] = []
    for entry in entries:
        if entry.parent_id is not None and entry.parent_id not in known:
            orphaned.append(entry.id)
            continue
        kept.append(entry)
        known.add(entry.id)

    # Rewind the leaf to the newest entry whose target still resolves; a
    # `leaf` entry pointing at a pruned target would fail `get_leaf_id`.
    leaf_id = None
    for entry in reversed(kept):
        candidate = _leaf_id_after_entry(entry)
        if candidate is None or candidate in known:
            leaf_id = candidate
            break

    recovery = SessionRecovery(
        file_path=file_path,
        skipped_lines=tuple(skipped),
        orphaned_entries=tuple(orphaned),
        unterminated_tail=not ends_with_newline,
    )
    return _LoadedSession(
        header=header,
        entries=kept,
        leaf_id=leaf_id,
        recovery=recovery,
        ends_with_newline=ends_with_newline,
    )


class JsonlSessionStorage(SessionStorage[JsonlSessionMetadata]):
    """Pi ``JsonlSessionStorage`` (``jsonl-storage.ts:161-293``).

    Per-instance ``asyncio.Lock`` is the Aelix-additive safety net around
    appends — Pi has no lock. It orders this instance's writers; it knows
    nothing of a second process on the same file (#137).

    One ``append_entry`` or ``set_leaf_id`` is one line in one
    ``append_file`` call. The line is serialized before the file is touched
    (an entry that cannot be encoded raises ``invalid_entry`` with nothing
    written), and the in-memory state changes only once the write
    returned. A write that fails part-way leaves at most a fragment with no
    newline; the next append opens a fresh line first, so the loader skips
    the fragment alone and prunes nothing (ADR-0242, extending ADR-0208).

    Every write runs under the lock and starts by settling a pending outcome
    (:meth:`_resolve_locked`, #721). :meth:`append_to_leaf` and
    :meth:`place_entry` choose the parent after that, inside the same hold,
    which is what keeps a line that landed and then raised on the path.
    :meth:`withdraw_tail` reads the path, moves the leaf and appends what
    survives in one hold, and what an interrupted one still owes is written
    before any other write.
    """

    def __init__(
        self,
        fs: FileSystem,
        file_path: str,
        header: _SessionHeader,
        entries: list[SessionTreeEntry],
        leaf_id: str | None,
        recovery: SessionRecovery | None = None,
        ends_with_newline: bool = True,
    ) -> None:
        self._fs = fs
        self._file_path = file_path
        #: Set when this file had to be repaired on load (Track S1);
        #: ``None`` for a clean file.
        self.recovery = recovery
        self._metadata = _header_to_metadata(header, file_path)
        self._entries: list[SessionTreeEntry] = list(entries)
        self._by_id: dict[str, SessionTreeEntry] = {
            e.id: e for e in self._entries
        }
        self._labels_by_id: dict[str, str] = {}
        for entry in self._entries:
            _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id: str | None = leaf_id
        self._lock = asyncio.Lock()
        # A file that ends mid-line will fuse the next append onto its last
        # line, and the load after that drops the fused result — losing the
        # appended entry AND the previously durable one it landed on. Keyed
        # only on the trailing byte, NOT on `recovery`: an unterminated file
        # whose lines all happen to parse is still unterminated, and that
        # case loses already-committed data. Cleared once the healing
        # newline has been written.
        self._needs_newline = not ends_with_newline
        # The entry whose write raised after ``append_file`` began, until the
        # next write reads the file back and adopts it or drops it (#721).
        # One slot is enough: no write starts while it is set.
        self._pending: _Pending | None = None
        # What an interrupted :meth:`withdraw_tail` still has to write. Every
        # write finishes it before it does anything else (#721).
        self._replay: _Replay | None = None

    async def _append_line(
        self, entry: SessionTreeEntry, what: str, *, snapshot: bool = False
    ) -> str:
        """Append ``entry`` as one JSON line in one ``append_file`` call, healing first.

        Serialized before the file is touched, so an entry that cannot be
        encoded — ``entry_to_json`` or ``json.dumps`` refusing it — raises
        ``invalid_entry`` with nothing written and the heal flag as it was.
        After that, ANY exception out of the write — an
        ``OSError``, a ``KeyboardInterrupt`` between two ``os.write`` calls, a
        cancelled awaiting ``FileSystem`` — may have left a fragment with no
        newline, so the flag is re-armed before re-raising and the next append
        opens a fresh line. It used to stay down: the next entry was glued onto
        the fragment, the fused line failed to parse, and every entry parented
        below it was pruned on the next load (#294, ADR-0242). This relies on
        the ``FileSystem`` contract that the write is over once the call is:
        the lock is released here, and bytes a background writer added later
        would land inside the next line.

        The failure classes (#721, ADR-0242 rule 5):

        - :class:`~aelix_agent_core.session.fs.NotAttempted` — nothing was
          written. ``SessionError("storage")``; nothing pending, the heal flag
          as it was, and the same write can be retried.
        - any other ``OSError`` — ``SessionError("outcome_unknown")``, with
          ``entry`` left pending for the next write to settle.
        - anything else, ``CancelledError``, ``KeyboardInterrupt`` and
          ``SystemExit`` among them — re-raised as itself, unchanged, with
          ``entry`` left pending the same way. A cancel stays a cancel.

        Returns the line as encoded, without the healing newline. ``snapshot``
        is recorded with a pending entry: :meth:`_settle_locked` then adopts
        the line it reads back rather than ``entry`` itself.
        """

        line = _encode_entry(entry, what)
        written = "\n" + line if self._needs_newline else line
        try:
            await self._fs.append_file(self._file_path, written)
        except NotAttempted as exc:
            raise SessionError(
                "storage",
                f"Failed to append session {what}: {exc}",
                cause=exc,
            ) from exc
        except BaseException as exc:
            self._needs_newline = True
            self._pending = _Pending(entry, line, snapshot)
            if isinstance(exc, OSError):
                raise SessionError(
                    "outcome_unknown",
                    f"Failed to append session {what}, and it may have reached "
                    f"the file anyway: {exc}",
                    cause=exc,
                ) from exc
            raise
        self._needs_newline = False
        return line

    def _commit_locked(self, entry: SessionTreeEntry) -> None:
        """Take ``entry`` into memory as the newest line. Caller holds the lock."""

        self._entries.append(entry)
        self._by_id[entry.id] = entry
        _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id = _leaf_id_after_entry(entry)

    async def _write_locked(self, entry: SessionTreeEntry, what: str) -> None:
        await self._append_line(entry, what)
        self._commit_locked(entry)

    async def _resolve_locked(self) -> None:
        """Settle what an earlier write left open before this one starts. Lock held.

        Two things, in order: the pending outcome (:meth:`_settle_locked`),
        then the rest of a :meth:`withdraw_tail` it interrupted
        (:meth:`_finish_replay_locked`). Either can raise, and then the write
        that asked is refused with nothing of its own written.
        """

        if self._pending is not None:
            await self._settle_locked()
        if self._replay is not None:
            await self._finish_replay_locked()

    async def _settle_locked(self) -> None:
        """Settle the pending outcome from the file itself. Lock held.

        The file is read back with the loader's own rule
        (:func:`_parse_jsonl_content`). The line landed when the exact line
        that was written is whole in the file and the loader keeps an entry
        with its id: it is then taken into memory as the newest line, and the
        leaf moves to it exactly as a reload would move it. If not, it never
        reached the file whole, and it is dropped; the heal flag stays armed,
        so the next write opens a fresh line after any fragment it left.
        Matching the line and not only the id keeps a write that reused a
        stored id, and did not land, from adopting the older line.

        What is taken in is the caller's own entry while it still encodes to
        that line — the harness tells its messages apart by identity — and
        otherwise, or for a placement (``snapshot``), the entry the loader
        parsed from the file: a payload the caller changed before the entry is
        settled does not reach memory. Once the caller's own entry is taken in
        it is memory's, as after any append that succeeded, and a change made
        after that reaches memory and not the file.

        A read that fails leaves the entry pending and raises
        ``SessionError("outcome_unknown")``: the write that asked is refused
        with nothing written, and the leaf stays where it was until a read
        succeeds. A cancel during the read propagates, pending kept.
        """

        pending = self._pending
        assert pending is not None
        try:
            content = await self._fs.read_text_file(self._file_path)
            loaded = _parse_jsonl_content(content, self._file_path)
        except (OSError, UnicodeError, SessionError) as exc:
            raise SessionError(
                "outcome_unknown",
                f"Previous append of entry {pending.entry.id} unresolved: the "
                f"session file could not be read back ({exc})",
                cause=exc,
            ) from exc
        self._pending = None
        landed: SessionTreeEntry | None = None
        if f"\n{pending.line}" in f"\n{content}\n":
            landed = next(
                (entry for entry in reversed(loaded.entries) if entry.id == pending.entry.id),
                None,
            )
        replay = self._replay
        if landed is None:
            if replay is not None and not replay.moved:
                # The withdrawal's leaf move never reached the file: it did not
                # happen, and the path is the one it started from.
                self._replay = None
            return
        if pending.snapshot or not _encodes_to(pending.entry, pending.line):
            self._commit_locked(landed)
        else:
            self._commit_locked(pending.entry)
        if replay is not None:
            if replay.moved:
                replay.survivors.pop(0)
            else:
                replay.moved = True

    async def _finish_replay_locked(self) -> None:
        """Append what an interrupted :meth:`withdraw_tail` still owes. Lock held.

        Each survivor goes on the leaf the one before it left. A write that
        raises leaves the rest owed, the failed one first, and the next write
        finishes it: a survivor whose line landed is taken in by
        :meth:`_settle_locked` and not written again. A survivor that cannot be
        encoded never will be, so it is logged and skipped rather than owed
        for ever.
        """

        replay = self._replay
        assert replay is not None and replay.moved
        while replay.survivors:
            template = replay.survivors[0]
            entry = replace(
                template, id=_generate_entry_id(self._by_id), parent_id=self._leaf_locked()
            )
            try:
                await self._write_locked(entry, f"entry {entry.id}")
            except SessionError as exc:
                if exc.code != "invalid_entry":
                    raise
                _LOG.warning(
                    "withdrawal skipped entry %s, which cannot be written again: %s",
                    template.id,
                    exc,
                )
            replay.survivors.pop(0)
        self._replay = None

    def _leaf_locked(self) -> str | None:
        """:meth:`get_leaf_id`'s answer, for a write that is about to parent on it."""

        if self._current_leaf_id is not None and self._current_leaf_id not in self._by_id:
            raise SessionError(
                "invalid_session",
                f"Entry {self._current_leaf_id} not found",
            )
        return self._current_leaf_id

    @classmethod
    async def open(
        cls, fs: FileSystem, file_path: str
    ) -> JsonlSessionStorage:
        loaded = await _load_jsonl_storage(fs, file_path)
        return cls(
            fs,
            file_path,
            loaded.header,
            loaded.entries,
            loaded.leaf_id,
            loaded.recovery,
            ends_with_newline=loaded.ends_with_newline,
        )

    @classmethod
    async def create(
        cls,
        fs: FileSystem,
        file_path: str,
        *,
        cwd: str,
        session_id: str,
        parent_session_path: str | None = None,
        entries: Sequence[SessionTreeEntry] = (),
    ) -> JsonlSessionStorage:
        """Publish a new session file holding the header and ``entries``.

        One publish (:func:`_publish_file`): the file appears with every
        line or does not appear. The bytes are the ones the header followed
        by one ``append_entry`` per entry would have written; the returned
        instance holds ``entries`` and the leaf those appends would have
        left. Every line is serialized before anything is staged, so an
        entry that cannot be encoded raises ``invalid_entry`` and no
        file — no ``.jsonl``, no ``.tmp`` — is written. A failed write or
        rename raises ``storage``.
        """

        header = _SessionHeader(
            id=session_id,
            timestamp=_iso_now(),
            cwd=cwd,
            parent_session=parent_session_path,
        )
        wire: dict[str, Any] = {
            "type": "session",
            "version": 3,
            "id": header.id,
            "timestamp": header.timestamp,
            "cwd": header.cwd,
        }
        if header.parent_session is not None:
            wire["parentSession"] = header.parent_session
        lines = [json.dumps(wire) + "\n"]
        leaf_id: str | None = None
        for entry in entries:
            lines.append(_encode_entry(entry, f"entry {entry.id}"))
            leaf_id = _leaf_id_after_entry(entry)
        content = "".join(lines)
        # ``lines`` and ``content`` are each a whole copy of the file, and
        # ``write_file`` encodes a third. Dropping the list before staging
        # keeps a fork's peak memory at three times the file, where the
        # append loop this replaced had it, not four.
        del lines
        await _publish_file(fs, file_path, content)
        return cls(fs, file_path, header, list(entries), leaf_id)

    async def get_metadata(self) -> JsonlSessionMetadata:
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        if self._current_leaf_id is not None and self._current_leaf_id not in self._by_id:
            raise SessionError(
                "invalid_session",
                f"Entry {self._current_leaf_id} not found",
            )
        return self._current_leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        async with self._lock:
            await self._resolve_locked()
            if leaf_id is not None and leaf_id not in self._by_id:
                raise SessionError("not_found", f"Entry {leaf_id} not found")
            entry = LeafEntry(
                id=_generate_entry_id(self._by_id),
                parent_id=self._current_leaf_id,
                timestamp=_iso_now(),
                target_id=leaf_id,
            )
            await self._write_locked(entry, f"leaf {entry.id}")

    async def create_entry_id(self) -> str:
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        """Append ``entry`` exactly as given, parent included.

        A pending outcome is settled first, but ``entry`` was built by its
        caller and keeps the parent it names: a caller that read the leaf
        before an outcome was settled gets the parent it read. ``Session``
        never does that; it builds inside :meth:`append_to_leaf`.
        """

        async with self._lock:
            await self._resolve_locked()
            await self._write_locked(entry, f"entry {entry.id}")

    async def append_to_leaf(
        self, build: Callable[[str, str | None], SessionTreeEntry]
    ) -> SessionTreeEntry:
        """Build an entry on the current leaf and append it, in one hold of the lock (#721).

        In order: settle a pending outcome, call ``build(new_id, leaf)``,
        append what it returns, move the leaf. Building inside the hold is the
        point: an entry built before an unknown outcome was settled carries
        the leaf from before it, and appending that one made it a sibling of
        a line already in the file, which then left the current branch.
        """

        async with self._lock:
            await self._resolve_locked()
            entry = build(_generate_entry_id(self._by_id), self._leaf_locked())
            await self._write_locked(entry, f"entry {entry.id}")
            return entry

    async def place_entry(
        self,
        entry_id: str,
        build: Callable[[str | None], SessionTreeEntry],
        *,
        same: Callable[[SessionTreeEntry], bool],
    ) -> Placement:
        """Write the entry with id ``entry_id`` once; the same payload again is adopted (#721).

        Under one hold of the lock: settle a pending outcome; if ``entry_id``
        is already stored, return it adopted when ``same(existing)`` and raise
        :class:`PlacementConflict` otherwise, writing nothing either way;
        else build it on the current leaf and append it. So a placement whose
        own write had an unknown outcome is simply placed again: the second
        call finds the first one's line, or writes it.

        What is stored, and returned, is the entry parsed back from the line
        written — never the built object, whose payload its caller still
        holds and may change. So ``same`` always compares against what is in
        the file, in this process as after a reload (Codex review of #721,
        finding 2).
        """

        async with self._lock:
            await self._resolve_locked()
            existing = self._by_id.get(entry_id)
            if existing is not None:
                if same(existing):
                    return Placement(existing, adopted=True)
                raise PlacementConflict(entry_id)
            entry = build(self._leaf_locked())
            if entry.id != entry_id:
                raise ValueError(f"place_entry built {entry.id!r} for id {entry_id!r}")
            line = await self._append_line(entry, f"entry {entry.id}", snapshot=True)
            try:
                stored = _parse_entry_line(line, self._file_path, len(self._entries) + 2)
            except SessionError:
                # A line this store's own loader would skip (a ``build`` that
                # left the timestamp empty). Kept as built, as any append is.
                stored = entry
            self._commit_locked(stored)
            return Placement(stored, adopted=False)

    async def withdraw_tail(
        self,
        target_leaf: str | None,
        is_withdrawn: Callable[[SessionTreeEntry], bool],
    ) -> None:
        """Move the leaf back to ``target_leaf`` and append again what ``is_withdrawn`` keeps (#721).

        ``Session.withdraw_tail``'s operation (ADR-0286), as one hold of the
        lock: settle what is open, read the path, move the leaf, append each
        survivor with a fresh id on the leaf the one before it left. Nothing
        else writes in between, so an append that came in meanwhile is not
        left on the branch behind.

        Raises ``not_found`` when ``target_leaf`` is not on the current path,
        with nothing written. Once the leaf move is written the withdrawal is
        owed until it is complete: a write that raises after that keeps the
        rest, and the next write — or :meth:`resolve_pending` — finishes it
        before doing its own. A leaf move whose outcome was unknown and that
        did not land is a withdrawal that did not happen. Before #721 the
        survivors were appended by the caller after a separate leaf move, and
        a move whose line landed and then raised left them off the branch.
        """

        async with self._lock:
            await self._resolve_locked()
            path = await self.get_path_to_root(self._leaf_locked())
            if target_leaf is None:
                tail = path
            else:
                ids = [entry.id for entry in path]
                if target_leaf not in ids:
                    raise SessionError(
                        "not_found", f"Entry {target_leaf} is not on the current path"
                    )
                tail = path[ids.index(target_leaf) + 1 :]
            survivors = [entry for entry in tail if not is_withdrawn(entry)]
            move = LeafEntry(
                id=_generate_entry_id(self._by_id),
                parent_id=self._current_leaf_id,
                timestamp=_iso_now(),
                target_id=target_leaf,
            )
            try:
                await self._write_locked(move, f"leaf {move.id}")
            except BaseException:
                if self._pending is not None:
                    self._replay = _Replay(survivors, moved=False)
                raise
            self._replay = _Replay(survivors, moved=True)
            await self._finish_replay_locked()

    async def resolve_pending(self) -> None:
        """Settle what an earlier write left open, writing only an owed withdrawal (#721).

        See :meth:`_resolve_locked`: a pending outcome is read back from the
        file, and the rest of an interrupted :meth:`withdraw_tail` is appended.
        """

        async with self._lock:
            await self._resolve_locked()

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return self._by_id.get(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        return [e for e in self._entries if e.type == type]

    async def get_label(self, id: str) -> str | None:
        return self._labels_by_id.get(id)

    async def get_path_to_root(
        self, leaf_id: str | None
    ) -> list[SessionTreeEntry]:
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current = self._by_id.get(leaf_id)
        if current is None:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        while current is not None:
            path.insert(0, current)
            if not current.parent_id:
                break
            parent = self._by_id.get(current.parent_id)
            if parent is None:
                raise SessionError(
                    "invalid_session",
                    f"Entry {current.parent_id} not found",
                )
            current = parent
        return path

    async def get_entries(self) -> list[SessionTreeEntry]:
        return list(self._entries)


__all__ = [
    "JsonlSessionStorage",
    "SessionRecovery",
    "load_jsonl_session_metadata",
]
