"""Private occurrence ledger and request checkpoints for the opt-in CLI observer."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
import weakref
from collections import deque
from collections.abc import Callable, Coroutine, Iterable, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from types import MethodType
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, TypeVar

from aelix_ai.messages import AssistantMessage, ToolResultMessage, UserMessage

from aelix_agent_core.types import AgentMessage

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import TranscriptOutcome

LINE_BYTES = 1_048_576
GAP_BYTES = 1024
MAX_RECORDS = 128
RETAINED_BYTES = 4 * LINE_BYTES
_EPOCH_BYTES = 1536
_RECORD_BYTES = 4096
_WITNESS_BYTES = 512
_WITNESS_ITEM_BYTES = 128
_BYTES_OVERHEAD = sys.getsizeof(b"")

_P = ParamSpec("_P")
_T = TypeVar("_T")


@dataclass(slots=True)
class _InputOrigin:
    observation: InputObservation | None
    method: str
    source: str
    request_id: str | None

    def clear(self) -> None:
        self.observation = None
        self.request_id = None


_input_origin: ContextVar[_InputOrigin | None] = ContextVar("_input_origin", default=None)


def is_base_method(call: object, owner: object, implementation: object) -> bool:
    return isinstance(call, MethodType) and call.__self__ is owner and call.__func__ is implementation


def call_with_input_origin(
    observation: InputObservation | None,
    method: str,
    source: str,
    request_id: str | None,
    owner: object,
    implementation: object,
    call: Callable[_P, Coroutine[Any, Any, _T]],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> Coroutine[Any, Any, _T]:
    """Attach provenance only when dispatch directly reaches the base admission path."""
    if observation is None:
        return call(*args, **kwargs)

    async def run() -> _T:
        if not is_base_method(call, owner, implementation):
            # A host may add, substitute or defer admissions. Retire the epoch
            # before forwarding so no dependent input can claim this origin.
            observation.gap("unverifiable")
            return await call(*args, **kwargs)
        origin = _InputOrigin(observation, method, source, request_id)
        token = _input_origin.set(origin)
        try:
            return await call(*args, **kwargs)
        finally:
            origin.clear()
            _input_origin.reset(token)

    return run()


def take_input_origin(
    observation: InputObservation | None, method: str, default_source: str
) -> tuple[str, str | None]:
    origin = _input_origin.get() if observation is not None else None
    if origin is None or origin.observation is not observation or origin.method != method:
        return default_source, None
    # Do not let later admissions inherit the consumed origin.
    result = origin.source, origin.request_id
    origin.clear()
    return result


InputEvent = Literal[
    "prompt_started", "queued", "consumed", "input_settled", "prompt_settled", "stream_gap"
]


class LineTooLarge(ValueError):
    pass


def _json_chunks(value: Any) -> Iterator[bytes]:
    """Encode the session message dataclass representation in bounded chunks."""

    if isinstance(value, str):
        yield b'"'
        for start in range(0, len(value), 8192):
            yield json.dumps(value[start : start + 8192], ensure_ascii=False)[1:-1].encode()
        yield b'"'
    elif is_dataclass(value) and not isinstance(value, type):
        yield from _json_object((field.name, getattr(value, field.name)) for field in fields(value))
    elif isinstance(value, dict):
        yield from _json_object(value.items())
    elif isinstance(value, (tuple, list)):
        yield b"["
        for index, item in enumerate(value):
            if index:
                yield b","
            yield from _json_chunks(item)
        yield b"]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _json_object(items: Iterable[tuple[str, Any]]) -> Iterator[bytes]:
    yield b"{"
    for index, (key, value) in enumerate(items):
        if index:
            yield b","
        if not isinstance(key, str):
            raise TypeError("Input observation JSON keys must be strings")
        yield from _json_chunks(key)
        yield b":"
        yield from _json_chunks(value)
    yield b"}"


def encode_line(frame: dict[str, Any], *, limit: int = LINE_BYTES) -> bytes:
    wire = bytearray()
    for chunk in _json_chunks(frame):
        if len(wire) + len(chunk) >= limit:
            raise LineTooLarge
        wire.extend(chunk)
    wire.append(10)
    return bytes(wire)


@dataclass
class _Body:
    digest: bytes | None
    data: bytes | None
    reason: str | None = None

    @property
    def retained_bytes(self) -> int:
        return sys.getsizeof(self.data) if self.data is not None else 0

    @classmethod
    def snapshot(cls, message: UserMessage, *, inline_budget: int | None = None) -> _Body:
        digest = hashlib.sha256()
        body = bytearray() if inline_budget is not None else None
        limit = min(inline_budget or 0, LINE_BYTES - 1)
        size = 0
        try:
            for chunk in _json_chunks(message):
                digest.update(chunk)
                size += len(chunk)
                if body is not None:
                    if size > limit:
                        body = None
                    else:
                        body.extend(chunk)
        except (TypeError, ValueError, RecursionError):
            return cls(None, None, "bodyUnencodable")
        if inline_budget is not None:
            if size >= LINE_BYTES:
                return cls(digest.digest(), None, "bodyTooLarge")
            if size > inline_budget:
                return cls(digest.digest(), None, "budgetExceeded")
        return cls(digest.digest(), bytes(body) if body is not None else None)


@dataclass(eq=False)
class InputAttempt:
    input_id: str
    attempt: int
    source: str
    route: str
    request_id: bytes | None = None
    queued_prompt: str | None = None
    owned: bool = False
    stream_id: str | None = None
    turn: int = 0
    ordinal: int = 0
    position: int = -1
    body: _Body | None = None
    placed: bool = False
    decision: str | None = None
    entry_id: str | None = None
    stored_matches: bool = False
    returnable: bool = False

    def identity(self) -> dict[str, Any]:
        return {"inputId": self.input_id, "attempt": self.attempt}

    def origin(self) -> dict[str, Any]:
        result = {**self.identity(), "source": self.source, "route": self.route}
        if self.request_id is not None:
            result["requestId"] = self.request_id.decode("utf-8")
        return result


class _Witness:
    """Weak references preserve positional lineage without retaining message bodies."""

    def __init__(self, messages: Sequence[AgentMessage]) -> None:
        self.original = tuple(weakref.ref(message) for message in messages)
        self.valid = True

    def arbitrary(self) -> None:
        if self.valid:
            self.valid = len({id(ref()) for ref in self.original}) == len(self.original)

    def check(self, messages: Sequence[AgentMessage], *, default_filter: bool = False) -> None:
        if not self.valid:
            return
        original = [ref() for ref in self.original]
        if default_filter:
            original = [
                msg
                for msg in original
                if isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage))
            ]
        self.valid = len(original) == len(messages) and all(
            old is new for old, new in zip(original, messages, strict=True)
        )


class InputObservation:
    """One epoch on one harness. All ledger updates and wire fences are synchronous."""

    def __init__(
        self,
        emit: Callable[[InputEvent, dict[str, Any], bytes | None], str | None],
        settle_entry: Callable[[InputAttempt], dict[str, Any]],
        begin_cut: Callable[[set[str]], None],
        valid_request_id: Callable[[object], bool],
    ) -> None:
        self._emit = emit
        self._settle_entry = settle_entry
        self._begin_cut = begin_cut
        self._valid_request_id = valid_request_id
        self.active = True
        self.prompt_id: str | None = None
        self.prompt_outcome = "failed"
        self._records: set[InputAttempt] = set()
        self._queued: dict[int, deque[InputAttempt]] = {}
        self._detached: dict[int, deque[InputAttempt]] = {}
        self._direct: InputAttempt | None = None
        self._prefix_count = 0
        self._direct_message: weakref.ReferenceType[AgentMessage] | None = None
        self._selected: tuple[InputAttempt, str, int, int | None] | None = None
        self._refused = False
        self._last_assistant: str | None = None
        self._assistant_open = False
        self._assistant_interrupted = False
        self._unanswered: list[tuple[int, InputAttempt]] = []
        self._pending: list[InputAttempt] = []
        self._receipt_target: tuple[UserMessage, InputAttempt] | None = None
        self._owned: list[InputAttempt] = []
        self._turn = 0
        self._ordinal = 0
        self._bytes = 0
        self._witness: _Witness | None = None

    def close(self) -> None:
        self.active = False
        for record in self._records:
            if record.body is not None:
                record.body.data = None
        self._bytes = 0
        self._records.clear()
        self._queued.clear()
        self._detached.clear()
        self._owned.clear()
        self._pending.clear()
        self._receipt_target = None
        self._unanswered.clear()
        self._direct = None
        self._selected = None
        self._witness = None
        self._direct_message = None

    def gap(self, reason: str) -> None:
        if self.active:
            self.close()
            self._emit("stream_gap", {"reason": reason}, None)

    @property
    def _retained_bytes(self) -> int:
        # Container capacities can outlive the attempts that grew them.
        containers = (
            self._records,
            self._queued,
            self._detached,
            self._owned,
            self._pending,
            self._unanswered,
        )
        witness = self._witness
        return (
            self._bytes
            + _EPOCH_BYTES
            + sum(map(sys.getsizeof, containers))
            + (_WITNESS_BYTES + len(witness.original) * _WITNESS_ITEM_BYTES if witness else 0)
        )

    def _fits(self) -> bool:
        if self._retained_bytes <= RETAINED_BYTES:
            return True
        self.gap("budgetExceeded")
        return False

    def _new(
        self, source: str, route: str, request_id: str | None, previous: InputAttempt | None = None
    ) -> InputAttempt | None:
        if not self.active:
            return None
        if (
            len(self._records) >= MAX_RECORDS
            or self._retained_bytes + _RECORD_BYTES > RETAINED_BYTES
        ):
            self.gap("budgetExceeded")
            return None
        record = InputAttempt(
            previous.input_id if previous else uuid.uuid4().hex,
            previous.attempt + 1 if previous else 1,
            source,
            route,
            request_id.encode("utf-8")
            if source == "rpc" and request_id is not None and self._valid_request_id(request_id)
            else None,
        )
        self._records.add(record)
        self._bytes += _RECORD_BYTES
        return record if self._fits() else None

    def _own(self, record: InputAttempt) -> None:
        if not record.owned:
            record.owned = True
            self._owned.append(record)

    def start(self, kind: str, source: str | None = None, request_id: str | None = None) -> None:
        if not self.active:
            return
        if self.prompt_id is not None:
            self.gap("unverifiable")
            return
        self.prompt_id = uuid.uuid4().hex
        self.prompt_outcome = "failed"
        info: dict[str, Any] = {"promptId": self.prompt_id, "promptKind": kind}
        if source is not None:
            self._direct = self._new(source, "prompt", request_id)
            if self._direct is not None:
                self._own(self._direct)
                info["input"] = self._direct.origin()
        if self.active and self._fits():
            self._emit("prompt_started", info, None)

    def set_outcome(self, outcome: str) -> None:
        self.prompt_outcome = outcome

    def enqueue(
        self,
        message: AgentMessage,
        source: str,
        route: str,
        request_id: str | None = None,
        *,
        previous: InputAttempt | None = None,
    ) -> None:
        if not isinstance(message, UserMessage):
            return
        record = self._new(source, route, request_id, previous)
        if record is None:
            return
        record.queued_prompt = self.prompt_id
        queue = self._queued.setdefault(id(message), deque())
        queue.append(record)
        if self._fits():
            self._emit("queued", {**record.origin(), "queuedPromptId": self.prompt_id}, None)

    @staticmethod
    def _take(items: dict[int, deque[InputAttempt]], message: AgentMessage) -> InputAttempt | None:
        key = id(message)
        found = items.get(key)
        if found is None:
            return None
        record = found.popleft()
        if not found:
            del items[key]
        return record

    def detach(self, messages: Iterable[AgentMessage]) -> None:
        for message in messages:
            if not self.active:
                return
            record = self._take(self._queued, message)
            if record is not None:
                self._own(record)
                self._detached.setdefault(id(message), deque()).append(record)
        if self.active:
            self._fits()

    def remove(self, messages: Iterable[AgentMessage]) -> None:
        for message in messages:
            record = self._take(self._queued, message)
            if record is not None:
                self._withdraw(record)

    def _withdraw(self, record: InputAttempt) -> None:
        record.decision = "withdrawn"
        if self.prompt_id is None:
            self._terminal(record)
        else:
            self._own(record)
            self._fits()

    def withdraw(
        self,
        committed: Sequence[tuple[AgentMessage, AgentMessage]],
        uncommitted: AgentMessage | None,
    ) -> None:
        # The native answered cut empties the list synchronously. Within that
        # suffix, each original occurrence was recorded in admission order.
        if uncommitted is not None:
            record = self._restore_slot(uncommitted)
            if record is not None:
                self._withdraw(record)
        if committed:
            for _, record in self._unanswered:
                self._withdraw(record)

    def answered(self) -> None:
        self._unanswered.clear()

    def bind_direct(self, message: AgentMessage, *, refused: bool) -> None:
        self._direct_message = weakref.ref(message)
        self._refused = refused

    def _restore_slot(self, message: AgentMessage) -> InputAttempt | None:
        if self._selected is not None and self._selected[3] == id(message):
            record, self._selected = self._selected[0], None
            return record
        if self._direct_message is not None and self._direct_message() is message:
            record, self._direct = self._direct, None
            return record
        return self._take(self._detached, message)

    def restore(self, messages: Sequence[AgentMessage]) -> None:
        records = []
        committed = deque(self._unanswered)
        for message in messages:
            prior = next(
                (
                    (key, item)
                    for key, item in committed
                    if key == id(message) and item.decision == "withdrawn"
                ),
                None,
            )
            if prior is None:
                record = self._restore_slot(message)
            else:
                committed.remove(prior)
                record = prior[1]
            if record is None:
                if self.active and isinstance(message, UserMessage):
                    self.gap("unverifiable")
                continue
            records.append((message, record))
        added: dict[int, deque[InputAttempt]] = {}
        for message, record in records:
            rest = self._queued.pop(id(message), None)
            request_id = (
                record.request_id.decode("utf-8") if record.request_id is not None else None
            )
            self.enqueue(message, record.source, record.route, request_id, previous=record)
            queue = self._queued.pop(id(message), None)
            if not self.active:
                return
            if queue:
                added.setdefault(id(message), deque()).extend(queue)
            if rest:
                self._queued[id(message)] = rest
        for key, slots in added.items():
            slots.extend(self._queued.get(key, ()))
            self._queued[key] = slots
        self._fits()

    def initial_prefix(self, count: int) -> None:
        self._prefix_count = count

    def before_event(self, event: Any) -> None:
        if event.type == "turn_start":
            self._turn += 1
        if event.type in ("message_start", "message_update", "message_end") and isinstance(
            event.message, AssistantMessage
        ):
            self.no_request()
            if event.type == "message_end":
                self._last_assistant = event.message.stop_reason

    def after_classic_event(self, event: Any) -> None:
        if event.type in ("turn_end", "agent_end"):
            self._assistant_interrupted |= self._assistant_open
        elif event.type in ("message_start", "message_update", "message_end") and isinstance(
            event.message, AssistantMessage
        ):
            if event.type == "message_start":
                self._assistant_interrupted |= self._assistant_open
            self._assistant_open = event.type != "message_end"

    def complete(self, aborted: bool) -> None:
        if self.prompt_outcome == "aborted" or self._last_assistant == "aborted":
            self.set_outcome("aborted")
            return
        if self._last_assistant == "error":
            self.set_outcome("aborted" if aborted else "failed")
        elif self._last_assistant is not None:
            self.set_outcome("complete")
        elif aborted:
            self.set_outcome("aborted")

    def select_slot(
        self, message: AgentMessage, stream_id: str, position: int, initial_index: int | None
    ) -> None:
        if not self.active or not isinstance(message, UserMessage):
            return
        self._drop_unannounced_prefix()
        new_prefix = initial_index is not None and initial_index < self._prefix_count
        if new_prefix:
            record = self._new("extension", "prefix", None)
        elif self._direct_message is not None and self._direct_message() is message:
            record, self._direct = self._direct, None
        else:
            record = self._take(self._detached, message)
        if record is not None:
            # Only a detached or direct native slot is eligible for restoration
            # from the pending-input list. A prefix can share its object.
            self._selected = record, stream_id, position, None if new_prefix else id(message)

    def _drop_unannounced_prefix(self) -> None:
        if self._selected is not None and not self._selected[0].owned:
            self._records.remove(self._selected[0])
            self._bytes -= _RECORD_BYTES
            self._selected = None

    def admit(
        self, original: AgentMessage, message: AgentMessage, stream_id: str | None, *,
        notice: bool, base_persistence: bool,
    ) -> None:
        if not self.active or notice or not isinstance(message, UserMessage):
            return
        selected, self._selected = self._selected, None
        if not base_persistence or selected is None or selected[1] != stream_id:
            self.gap("unverifiable")
            return
        record, record.stream_id, record.position, _ = selected
        self._own(record)
        self._ordinal += 1
        record.ordinal = self._ordinal
        record.turn = self._turn
        self._pending.append(record)
        self._unanswered.append((id(original), record))
        if not self._fits():
            return
        body = _Body.snapshot(
            message, inline_budget=RETAINED_BYTES - self._retained_bytes - _BYTES_OVERHEAD
        )
        if body.reason == "budgetExceeded":
            self.gap("budgetExceeded")
            return
        record.body = body
        self._bytes += body.retained_bytes
        self._receipt_target = message, record

    def take_receipt_target(self, message: AgentMessage) -> InputAttempt | None:
        # Admission and base persistence enter back-to-back without an await.
        # Consume this handoff before storage can suspend or re-enter the host.
        target, self._receipt_target = self._receipt_target, None
        return target[1] if target is not None and target[0] is message else None

    def witness(self, messages: Sequence[AgentMessage]) -> None:
        self._witness = None
        charge = _WITNESS_BYTES + len(messages) * _WITNESS_ITEM_BYTES
        if self._pending and self._retained_bytes + charge <= RETAINED_BYTES:
            try:
                self._witness = _Witness(messages)
            except TypeError:
                self._witness = None

    def arbitrary_context(self, enabled: bool) -> None:
        if self._witness is not None and enabled:
            self._witness.arbitrary()

    def check_context(self, messages: Sequence[AgentMessage]) -> None:
        if self._witness is not None:
            self._witness.check(messages)

    def request(self, messages: Sequence[AgentMessage], *, default_filter: bool) -> None:
        witness, self._witness = self._witness, None
        if self._refused:
            self.no_request()
            return
        if witness is not None:
            witness.check(messages, default_filter=default_filter)
        position = 0
        mapped: dict[int, UserMessage] = {}
        for index, ref in enumerate(witness.original if witness and witness.valid else ()):
            original = ref()
            if default_filter and not isinstance(
                original, (UserMessage, AssistantMessage, ToolResultMessage)
            ):
                continue
            candidate = messages[position]
            if isinstance(candidate, UserMessage):
                mapped[index] = candidate
            position += 1
        del witness
        self._flush(mapped, "contextMismatch")

    def no_request(self) -> None:
        self._witness = None
        self._flush({}, "noModelRequest")

    def _flush(self, mapped: dict[int, UserMessage], fallback: str) -> None:
        pending, self._pending = self._pending, []
        for record in pending:
            if not self.active:
                return
            self._placement(record, mapped.get(record.position), fallback)

    def _placement(self, record: InputAttempt, message: UserMessage | None, fallback: str) -> None:
        body = record.body
        assert body is not None
        fields = {
            **record.origin(),
            "queuedPromptId": record.queued_prompt,
            "consumedPromptId": self.prompt_id,
            "turnIndex": record.turn,
            "inputIndex": record.ordinal,
            "streamId": record.stream_id,
        }
        reason = fallback
        if message is not None:
            if body.digest is None:
                reason = "bodyUnencodable"
            elif _Body.snapshot(message).digest == body.digest:
                reason = body.reason
                if reason is None:
                    reason = self._emit("consumed", {**fields, "placement": "available"}, body.data)
                    record.placed = reason is None
        if self.active and not record.placed:
            self._emit("consumed", {**fields, "placement": "unavailable", "reason": reason}, None)
        self._bytes -= body.retained_bytes
        body.data = None

    def receipt(
        self, record: InputAttempt, outcome: TranscriptOutcome | None, entry_id: str | None
    ) -> None:
        if self.active and outcome is not None:
            self.persisted(
                record,
                outcome.status,
                entry_id,
                outcome.stored.entry.message if outcome.stored is not None else None,
            )

    def persisted(
        self, record: InputAttempt, status: str, entry_id: str | None, stored: AgentMessage | None
    ) -> None:
        record.decision = status
        record.entry_id = entry_id
        record.stored_matches = bool(
            isinstance(stored, UserMessage)
            and record.body is not None
            and record.body.digest is not None
            and _Body.snapshot(stored).digest == record.body.digest
        )

    def qualify(self, stream_id: str, returnable: bool) -> None:
        for record in self._owned:
            if record.stream_id == stream_id:
                record.returnable = returnable
                return

    def replayed(self, old_id: str, new_id: str, decoded: AgentMessage | None) -> bool:
        for record in self._owned:
            if record.entry_id == old_id and record.decision != "withdrawn":
                self.persisted(record, "stored", new_id, decoded)
                return True
        return False

    def qualify_entry(self, entry_id: str, returnable: bool) -> None:
        for record in self._owned:
            if record.entry_id == entry_id:
                record.returnable = returnable
                return

    def release(self, *, settled: bool) -> None:
        if not self.active or self.prompt_id is None:
            return
        if not settled or self._assistant_open or self._assistant_interrupted:
            self.gap("unverifiable")
            return
        self._drop_unannounced_prefix()
        self.no_request()
        if not self.active:
            return
        self._begin_cut({record.entry_id for record in self._owned if record.entry_id is not None})
        for record in self._owned:
            if not self.active:
                return
            self._terminal(record)
        if self.active:
            self._emit(
                "prompt_settled", {"promptId": self.prompt_id, "outcome": self.prompt_outcome}, None
            )
        self._owned.clear()
        self._detached.clear()
        self._unanswered.clear()
        self._direct = None
        self._selected = None
        self._direct_message = None
        self._last_assistant = None
        self._assistant_open = self._assistant_interrupted = False
        self._refused = False
        self._prefix_count = self._turn = self._ordinal = 0
        self.prompt_id = None

    def _terminal(self, record: InputAttempt) -> None:
        fields = {**record.identity(), "promptId": self.prompt_id}
        decision = record.decision
        if decision in ("withdrawn", "notStored"):
            data = {"outcome": decision}
        elif record.stream_id is None:
            data = {"outcome": "notAdmitted"}
        elif record.entry_id is not None:
            data = self._settle_entry(record)
        else:
            data = {"outcome": "unknown"}
        result = self._emit("input_settled", {**fields, **data}, None)
        if result is not None:
            self._emit("input_settled", {**fields, "outcome": "storageOnly"}, None)
        if self.active:
            self._records.remove(record)
            self._bytes -= _RECORD_BYTES
