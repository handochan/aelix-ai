"""The CLI-RPC binding and byte-bounded transcript/list control (ADR-0309)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, TranscriptOutcome
from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime
from aelix_agent_core.session.entries import MessageEntry
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_agent_core.session.storage import SessionError
from aelix_agent_core.session.transcript import StoredMessage, TranscriptSnapshot

from aelix_coding_agent.rpc._jsonl import encode_json_line
from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

METHOD = "transcript/list"
TRANSCRIPT_REPLY_MAX_BYTES = 1_048_576
TRANSCRIPT_PART_MAX_BYTES = 196_608
_MAX_FRAGMENT_CACHE = 32 * 1024 * 1024
_CURSOR = struct.Struct(">BQQQ32s16s")
_CURSOR_TAG_BYTES = 16
_CURSOR_BYTES = _CURSOR.size + _CURSOR_TAG_BYTES
_CURSOR_WIDTH = len(base64.urlsafe_b64encode(bytes(_CURSOR_BYTES)).rstrip(b"="))
_CURSOR_RE = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_COORDINATE_ERROR = "Transcript coordinates exceed reply bound"
_STALE_CURSOR = "Stale transcript cursor"
_INVALID_SELECTORS = "Invalid transcript selectors"
_WIDEST_REQUEST_ID = "\x00" * 170 + "xx"
BINDING_CHANGING = "Transcript binding is changing"
BINDING_UNAVAILABLE = "Transcript unavailable on this binding"
_EVENTS = frozenset(
    {
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
    }
)


class TranscriptError(ValueError):
    """A bounded, public method error."""


def request_id_valid(request_id: object) -> bool:
    if not isinstance(request_id, str) or not request_id or len(request_id) > 1022:
        return False
    try:
        request_id.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(json.dumps(request_id, ensure_ascii=True)) <= 1024


def _count(params: dict[str, Any], key: str, default: int, maximum: int) -> int:
    value = params.get(key, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise TranscriptError(_INVALID_SELECTORS)
    return value


@dataclass(frozen=True)
class _Position:
    sequence: int
    offset: int = 0


@dataclass(frozen=True)
class _Selector:
    limit: int
    leaf: str | None
    active: bool
    position: _Position
    cursor: str | None = None

    @classmethod
    def parse(cls, params: dict[str, Any]) -> _Selector:
        if params.keys() - {"limit", "leafId", "firstSequence", "cursor"}:
            raise TranscriptError(_INVALID_SELECTORS)
        limit = _count(params, "limit", 50, 100)
        if "cursor" in params:
            cursor = params["cursor"]
            if params.keys() - {"cursor", "limit"}:
                raise TranscriptError(_INVALID_SELECTORS)
            if not isinstance(cursor, str) or not _CURSOR_RE.fullmatch(cursor):
                raise TranscriptError(_STALE_CURSOR)
            return cls(limit, None, False, _Position(1), cursor)
        leaf = params.get("leafId")
        if leaf is not None and (not isinstance(leaf, str) or not leaf):
            raise TranscriptError(_INVALID_SELECTORS)
        sequence = _count(params, "firstSequence", 1, 2**64 - 1)
        return cls(limit, leaf, "leafId" not in params, _Position(sequence))


def _wire_size(data: Any) -> int:
    return len(encode_json_line(data, strict_utf8=True))


def _recovery_key(snapshot: TranscriptSnapshot) -> bytes:
    state = (snapshot.skipped_lines, snapshot.pruned_ids, snapshot.unterminated_tail)
    return hashlib.sha256(json.dumps(state).encode("ascii")).digest()


def _leaf_key(leaf: str | None) -> bytes:
    return hashlib.sha256(json.dumps(leaf).encode("ascii")).digest()[:16]


@dataclass(frozen=True)
class _Page:
    binding_id: str
    native_session_id: str
    leaf: str | None
    total: int
    recovered: bool
    request_id: str

    def data(self, entries: list[dict[str, Any]], cursor: str | None) -> dict[str, Any]:
        data = {
            "bindingId": self.binding_id,
            "nativeSessionId": self.native_session_id,
            "leafId": self.leaf,
            "totalMessages": self.total,
            "entries": entries,
            "nextCursor": cursor,
        }
        if self.recovered:
            data["recovered"] = True
        return data

    def coordinate_size(self) -> int:
        try:
            size = self.size([], None)
        except UnicodeError as exc:
            raise TranscriptError(_COORDINATE_ERROR) from exc
        if size > TRANSCRIPT_REPLY_MAX_BYTES:
            raise TranscriptError(_COORDINATE_ERROR)
        return size

    def size(self, entries: list[dict[str, Any]], cursor: str | None) -> int:
        return _wire_size(
            RpcSuccessResponse(
                id=self.request_id, command="aelix_control", data=self.data(entries, cursor)
            ).to_json()
        )


def _part(
    coordinates: dict[str, Any], offset: int, total_bytes: int, encoded: str
) -> dict[str, Any]:
    return {
        **coordinates,
        "messagePart": {"offset": offset, "totalBytes": total_bytes, "base64": encoded},
    }


class _Binding:
    def __init__(self, harness: AgentHarness, storage: JsonlSessionStorage) -> None:
        self.harness = harness
        self.storage = storage
        self.id = uuid.uuid4().hex
        self._key = uuid.uuid4().bytes
        self._fragment: tuple[str, bytes] | None = None
        self._path: tuple[TranscriptSnapshot, str | None, list[MessageEntry]] | None = None

    def coordinates_fit(self) -> bool:
        page = _Page(self.id, self.storage.native_session_id, None, 0, True, _WIDEST_REQUEST_ID)
        try:
            page.coordinate_size()
        except TranscriptError:
            return False
        return True

    def annotation(
        self, event: Any, stream: str | None, outcome: TranscriptOutcome | None
    ) -> dict[str, Any] | None:
        if stream is None or event.type not in _EVENTS:
            return None
        data: dict[str, Any] = {
            "nativeSessionId": self.storage.native_session_id,
            "bindingId": self.id,
            "streamId": stream,
        }
        if outcome is not None:
            data["status"] = outcome.status
            if outcome.stored is not None:
                if self._returnable(outcome.stored):
                    data.update(outcome.stored.coordinates(), replaced=outcome.replaced)
                else:
                    data["status"] = "storageOnly"
        return data

    def _returnable(self, stored: StoredMessage) -> bool:
        row, payload = stored.encode()
        page = _Page(
            self.id,
            self.storage.native_session_id,
            stored.entry.id,
            stored.sequence,
            True,
            _WIDEST_REQUEST_ID,
        )
        try:
            if (payload is None or len(payload) <= TRANSCRIPT_REPLY_MAX_BYTES) and page.size(
                [row], None
            ) <= TRANSCRIPT_REPLY_MAX_BYTES:
                return True
            if payload is None:
                return False
            # A fragment later in the directed scan can have the largest
            # offset, with the fixed-width continuation and padded base64.
            part = _part(stored.coordinates(), len(payload) - 1, len(payload), "AAAA")
            return page.size([part], "x" * _CURSOR_WIDTH) <= TRANSCRIPT_REPLY_MAX_BYTES
        except (UnicodeError, RecursionError, ValueError):
            return False

    def _encode_cursor(
        self, slot: int, leaf: str | None, recovery: bytes, position: _Position
    ) -> str:
        body = _CURSOR.pack(1, slot, position.sequence, position.offset, recovery, _leaf_key(leaf))
        tag = hmac.digest(self._key, body, "sha256")[:_CURSOR_TAG_BYTES]
        return base64.urlsafe_b64encode(body + tag).decode("ascii").rstrip("=")

    def _decode_cursor(
        self, cursor: str, snapshot: TranscriptSnapshot, recovery: bytes
    ) -> tuple[str | None, _Position]:
        if len(cursor) != _CURSOR_WIDTH:
            raise TranscriptError(_STALE_CURSOR)
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            body, tag = raw[:-_CURSOR_TAG_BYTES], raw[-_CURSOR_TAG_BYTES:]
            version, slot, sequence, offset, state, leaf_key = _CURSOR.unpack(body)
        except (binascii.Error, struct.error) as exc:
            raise TranscriptError(_STALE_CURSOR) from exc
        if version != 1 or state != recovery or not 0 <= slot <= len(snapshot.decoded.entries):
            raise TranscriptError(_STALE_CURSOR)
        leaf = snapshot.decoded.entries[slot - 1].id if slot else None
        if (
            leaf_key != _leaf_key(leaf)
            or not hmac.compare_digest(
                tag, hmac.digest(self._key, body, "sha256")[:_CURSOR_TAG_BYTES]
            )
            or cursor != self._encode_cursor(slot, leaf, recovery, _Position(sequence, offset))
        ):
            raise TranscriptError(_STALE_CURSOR)
        return leaf, _Position(sequence, offset)

    def _selection(
        self, selector: _Selector, snapshot: TranscriptSnapshot, recovery: bytes
    ) -> tuple[str | None, _Position]:
        if selector.cursor is not None:
            return self._decode_cursor(selector.cursor, snapshot, recovery)
        if selector.active:
            return snapshot.active_leaf(), selector.position
        return selector.leaf, selector.position

    def _messages(self, snapshot: TranscriptSnapshot, leaf: str | None) -> list[MessageEntry]:
        cached = self._path
        if cached is not None and cached[0] is snapshot and cached[1] == leaf:
            return cached[2]
        messages = snapshot.messages(leaf)
        self._path = (snapshot, leaf, messages)
        return messages

    def page(
        self, selector: _Selector, snapshot: TranscriptSnapshot, request_id: str
    ) -> dict[str, Any]:
        recovery = _recovery_key(snapshot)
        leaf, position = self._selection(selector, snapshot, recovery)
        messages = self._messages(snapshot, leaf)
        total = len(messages)
        if not 1 <= position.sequence <= total + 1 or (
            position.offset and position.sequence > total
        ):
            raise TranscriptError(_INVALID_SELECTORS)
        page = _Page(
            self.id, snapshot.native_session_id, leaf, total, snapshot.recovered, request_id
        )
        slot = 0 if leaf is None else snapshot.decoded.positions[leaf] + 1

        def continuation(next_position: _Position) -> str | None:
            if next_position.sequence > total:
                return None
            return self._encode_cursor(slot, leaf, recovery, next_position)

        try:
            return self._pack(page, messages, position, selector.limit, continuation)
        except UnicodeError as exc:
            raise TranscriptError(_COORDINATE_ERROR) from exc

    def _pack(
        self,
        page: _Page,
        messages: list[MessageEntry],
        position: _Position,
        limit: int,
        continuation: Callable[[_Position], str | None],
    ) -> dict[str, Any]:
        sequence = position.sequence
        empty_size = page.coordinate_size()
        if position.offset:
            stored = StoredMessage(messages[sequence - 1], sequence)
            cached = self._fragment
            if cached is not None and cached[0] == stored.entry.id:
                payload = cached[1]
            else:
                _, payload = stored.encode()
            if payload is None or position.offset >= len(payload):
                raise TranscriptError(_STALE_CURSOR)
            return self._fragment_page(page, stored, payload, position.offset, continuation)

        rows: list[dict[str, Any]] = []
        rows_size = 0
        for entry in messages[sequence - 1 : sequence - 1 + limit]:
            stored = StoredMessage(entry, sequence)
            row, payload = stored.encode()
            next_cursor = continuation(_Position(sequence + 1))
            cursor_overhead = _CURSOR_WIDTH - 2 if next_cursor else 0
            # No large reserialization if the compact payload alone cannot fit.
            row_size = (
                _wire_size(row) - 1
                if payload is None or len(payload) <= TRANSCRIPT_REPLY_MAX_BYTES
                else TRANSCRIPT_REPLY_MAX_BYTES
            )
            if empty_size + rows_size + row_size + cursor_overhead > TRANSCRIPT_REPLY_MAX_BYTES:
                if rows:
                    break
                if payload is None:
                    raise TranscriptError(_COORDINATE_ERROR)
                return self._fragment_page(page, stored, payload, 0, continuation)
            rows.append(row)
            rows_size += row_size + 2  # separator before a potential next row
            sequence += 1
        return page.data(rows, continuation(_Position(sequence)))

    def _fragment_page(
        self,
        page: _Page,
        stored: StoredMessage,
        payload: bytes,
        offset: int,
        continuation: Callable[[_Position], str | None],
    ) -> dict[str, Any]:
        total = len(payload)
        coordinates = stored.coordinates()
        frame = _part(coordinates, offset, total, "")
        remaining = total - offset
        byte_count = min(TRANSCRIPT_PART_MAX_BYTES, remaining)
        cursor = continuation(
            _Position(stored.sequence + 1)
            if byte_count == remaining
            else _Position(stored.sequence, offset + byte_count)
        )
        if page.size([frame], cursor) + ((byte_count + 2) // 3) * 4 > TRANSCRIPT_REPLY_MAX_BYTES:
            cursor = continuation(_Position(stored.sequence, offset + 1))
            room = TRANSCRIPT_REPLY_MAX_BYTES - page.size([frame], cursor)
            byte_count = min(byte_count, (room // 4) * 3)
            if byte_count < 1:
                raise TranscriptError(_COORDINATE_ERROR)
            cursor = continuation(_Position(stored.sequence, offset + byte_count))
        if total <= _MAX_FRAGMENT_CACHE:
            self._fragment = (stored.entry.id, payload)
        encoded = base64.b64encode(payload[offset : offset + byte_count]).decode("ascii")
        return page.data([_part(coordinates, offset, total, encoded)], cursor)


class TranscriptService:
    """One reader and one ordered output subscription for each installation."""

    def __init__(
        self,
        runtime: AgentSessionRuntime,
        output: Callable[[Any, dict[str, Any] | None], None],
    ) -> None:
        self.runtime = runtime
        self.output = output
        self.binding: _Binding | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._input_fence: Callable[[], None] | None = None

    async def start(self) -> None:
        await self.runtime.set_transcript_observer(self.install, self.unbind)

    async def install(self, harness: AgentHarness, writer_owned: bool) -> None:
        self.retire()
        session = harness.session if writer_owned else None
        storage = session.get_storage() if session is not None else None
        binding = _Binding(harness, storage) if isinstance(storage, JsonlSessionStorage) else None
        if binding is None or not binding.coordinates_fit():
            self._unsubscribe = harness.subscribe(lambda event: self.output(event, None))
            return

        await binding.storage.prepare_transcript_receipts()
        self.binding = binding

        def output(event: Any, stream: str | None, outcome: TranscriptOutcome | None) -> None:
            annotation = (
                binding.annotation(event, stream, outcome) if self.binding is binding else None
            )
            observation = getattr(harness, "_input_observation", None)
            if observation is not None and stream is not None and outcome is not None:
                observation.qualify(stream, annotation is not None and annotation.get("status") == "stored")
            self.output(event, annotation)
            if observation is not None:
                observation.after_classic_event(event)

        self._unsubscribe = harness.subscribe_transcript(output)

    def end_classic_output(self) -> None:
        if self.binding is None:
            self.retire()

    def unbind(self) -> None:
        if self._input_fence is not None:
            self._input_fence()
        if self.binding is not None:
            self.binding.storage.release_transcript_cut()
            self.binding = None

    def retire(self) -> None:
        self.unbind()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def available(self) -> bool:
        return (
            self.binding is not None
            and self.binding.harness is self.runtime.harness
            and self.runtime.stopped is None
            and not self.runtime.transcript_transitioning
        )

    @property
    def fence(self) -> tuple[int, str | None]:
        return self.runtime.transcript_epoch, self.binding.id if self.binding else None

    async def list(self, params: dict[str, Any], request_id: object) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id_valid(request_id):
            raise TranscriptError("Invalid transcript request ID")
        binding = self.binding
        if binding is None or not self.available:
            raise TranscriptError(BINDING_UNAVAILABLE)
        selector = _Selector.parse(params)
        fence = self.fence
        try:
            snapshot = await binding.storage.transcript_snapshot()
            if snapshot.native_session_id != binding.storage.native_session_id:
                raise TranscriptError(BINDING_CHANGING)
            result = binding.page(selector, snapshot, request_id)
        except SessionError as exc:
            if exc.code in ("invalidTree", "settlementPending"):
                raise TranscriptError(exc.code) from exc
            if exc.code == "not_found":
                raise TranscriptError("Unknown transcript leaf") from exc
            raise TranscriptError("Transcript unavailable") from exc
        except (OSError, UnicodeError, RecursionError) as exc:
            raise TranscriptError("Transcript unavailable") from exc
        if not self.available or self.fence != fence:
            raise TranscriptError(BINDING_CHANGING)
        return result
