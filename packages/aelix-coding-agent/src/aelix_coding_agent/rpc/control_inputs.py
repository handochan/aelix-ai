"""Bounded input placement and settlement frames on the CLI's R9 binding."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from aelix_agent_core.harness._input_observation import (
    GAP_BYTES,
    LINE_BYTES,
    InputAttempt,
    InputEvent,
    InputObservation,
    LineTooLarge,
    encode_line,
)
from aelix_agent_core.session.transcript import StoredMessage

from aelix_coding_agent.rpc.control_transcript import TranscriptService, request_id_valid

if TYPE_CHECKING:
    from aelix_coding_agent.rpc.control_transcript import _Binding

METHOD = "inputs/observe"
INVALID_ID = "Invalid input observation request ID"
INVALID_PARAMS = "Invalid input observation params"
BUSY = "Input observation requires an idle binding with empty queues"
UNSUPPORTED = f"Unsupported runtime control method: {METHOD}"


class InputObservationService:
    def __init__(self, transcript: TranscriptService, write: Callable[[bytes], None]) -> None:
        self.transcript = transcript
        self._write = write
        setter = getattr(transcript.runtime, "_set_input_observation_fence", None)
        self._set_fence: Callable[[Callable[[], None] | None], object] | None = (
            setter if callable(setter) else None
        )
        self.epoch: _Epoch | None = None

    @property
    def available(self) -> bool:
        return self._set_fence is not None and self.transcript.available

    def fence(self) -> None:
        if self.epoch is not None:
            self.epoch.fence()

    def configure(
        self, params: object, request_id: object, *, stable: bool, clean: bool
    ) -> dict[str, Any]:
        binding = self.transcript.binding
        set_fence = self._set_fence
        if binding is None or set_fence is None:
            raise ValueError(UNSUPPORTED)
        if not request_id_valid(request_id):
            raise ValueError(INVALID_ID)
        if not isinstance(params, dict) or params.keys() != {"enabled"}:
            raise ValueError(INVALID_PARAMS)
        enabled = params["enabled"]
        if type(enabled) is not bool:
            raise ValueError(INVALID_PARAMS)
        if not stable or not self.available:
            raise ValueError(BUSY)
        if enabled != (self.epoch is not None):
            if (
                not clean
                or not binding.harness._can_change_input_observation()
                or binding.storage.settlement_pending
                or binding.storage._lock.locked()
            ):
                raise ValueError(BUSY)
            if enabled:
                epoch = _Epoch(binding, self._write, self._detach)
                try:
                    set_fence(epoch.fence)
                    epoch.attach()
                    self.transcript.input_fence = epoch.fence
                except BaseException:
                    # Setters may assign and then raise. Retire the provisional
                    # epoch and clear every attachment before returning the error.
                    epoch.close()
                    raise
                self.epoch = epoch
                epoch.ledger.active = True
            else:
                assert self.epoch is not None
                self.epoch.close()
        return {
            "enabled": enabled,
            "subscriptionId": self.epoch.id if self.epoch is not None else None,
            "bindingId": binding.id if enabled else None,
        }

    def _detach(self) -> None:
        self.epoch = None
        with suppress(Exception):
            self.transcript.input_fence = None
        assert self._set_fence is not None
        # Even if the runtime cannot remove the callback, it is now inert.
        with suppress(Exception):
            self._set_fence(None)


class _Epoch:
    def __init__(
        self, binding: _Binding, write: Callable[[bytes], None], detach: Callable[[], None]
    ) -> None:
        self.id = uuid.uuid4().hex
        self.binding = binding
        self._seq = 0
        self._write = write
        self._detach = detach
        self._cut: dict[str, tuple[str | None, int | None]] = {}
        self.ledger = InputObservation(
            self._emit, self._settle_entry, self._begin_cut, request_id_valid
        )
        self.ledger.active = False

    def attach(self) -> None:
        self.binding.storage._input_replayed = self._replayed
        self.binding.harness._input_observation = self.ledger

    def fence(self) -> None:
        if self.binding.harness._input_observation is self.ledger:
            self.ledger.gap("bindingChanged")

    def close(self) -> None:
        self.ledger.close()
        with suppress(Exception):
            self.binding.harness._input_observation = None
        with suppress(Exception):
            self.binding.storage._input_replayed = None
        self._cut.clear()
        self._detach()

    def _begin_cut(self, wanted: set[str]) -> None:
        self._cut = self.binding.storage.settled_input_coordinates(wanted, limit=8192)

    def _settle_entry(self, record: InputAttempt) -> dict[str, Any]:
        assert record.entry_id is not None
        if record.entry_id not in self._cut:
            return {"outcome": "unknown"}
        parent, sequence = self._cut.pop(record.entry_id)
        if sequence is None or not record.stored_matches or not record.returnable:
            return {"outcome": "storageOnly"}
        return {
            "outcome": "committed",
            "nativeSessionId": self.binding.storage.native_session_id,
            "entryId": record.entry_id,
            "parentEntryId": parent,
            "sequence": sequence,
        }

    def _replayed(self, old_id: str, new_id: str, stored: StoredMessage | None) -> None:
        if not self.ledger.active or not self.ledger.replayed(
            old_id, new_id, stored.entry.message if stored else None
        ):
            return
        self.ledger.qualify_entry(new_id, stored is not None and self.binding._returnable(stored))

    def _emit(
        self, event: InputEvent, fields: dict[str, Any], body: bytes | None
    ) -> str | None:
        frame = {
            "type": "aelix_input",
            "event": event,
            "subscriptionId": self.id,
            "bindingId": self.binding.id,
            "seq": self._seq + 1,
            **fields,
        }
        try:
            wire = encode_line(frame, limit=GAP_BYTES if event == "stream_gap" else LINE_BYTES)
            if body is not None:
                addition = b',"message":'
                if len(wire) + len(addition) + len(body) > LINE_BYTES:
                    return "bodyTooLarge"
                wire = b"".join((wire[:-2], addition, body, b"}\n"))
        except LineTooLarge:
            return "bodyTooLarge"
        except (TypeError, ValueError, RecursionError):
            return "bodyUnencodable"
        try:
            self._write(wire)
        except Exception:  # noqa: BLE001 — a failed sink retires the epoch
            self.close()
        else:
            self._seq += 1
            if event == "stream_gap":
                self.close()
            elif event == "prompt_settled":
                self._cut.clear()
        return None
