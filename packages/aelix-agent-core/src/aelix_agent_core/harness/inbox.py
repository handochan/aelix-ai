"""The notice inbox: messages a producer posts for the model, placed at a turn boundary.

#611 S1b (#722, ADR-0293). A notice is what a piece of background work leaves
for the model when it ends: a framed body, a ``custom_type`` its producer names,
and a ``notice_id`` that is the producer's key for "this result". The kernel
knows nothing else about it. What produced it, how it was framed, and what the
id means are the producer's; nothing here branches on a kind of work.

Three rules make a notice reach the model once, and only after it is durable:

* **Placed before it is shown.** A notice becomes a ``custom_message`` entry in
  the session under an id derived from its ``notice_id``
  (:func:`notice_entry_id`) before the model sees it. The same id placed again —
  after a write whose outcome was unknown, or by a second process reading the
  same file — is adopted, not written twice (``SessionStorage.place_entry``,
  #721). One already on disk is never shown again.
* **Taken at a boundary, in arrival order, at most a budget at a time.** The
  harness takes pending notices where it takes steering and follow-up lines: at
  the start of a run, after each tool batch, and when the model has finished.
  One drain admits notices until :data:`NOTICE_DRAIN_BUDGET` would be passed,
  and always at least one.
* **Never withdrawn.** An abort, an Esc withdrawal (ADR-0286), a give-back of
  queued lines and a reload's queue hand-over do not touch the inbox: a notice
  is a fact about the past, not a line the user typed.

pi has a kernel inbox of the same shape in its durable harness
(``packages/durable/src/harness/inbox.ts`` at ``origin/main``): queued items
placed at the ``postTools`` and ``final`` boundaries, the placement recorded in
the same commit. It keeps user steers and writes in one table; aelix keeps its
existing steer and follow-up queues and adds this one beside them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

_log = logging.getLogger(__name__)

#: How many bytes of notices one boundary admits (ADR-0293): each notice is
#: charged its body and the most its placement line may add
#: (:attr:`Notice.finalize_max`), so what one request carries is bounded by
#: what was charged. The first notice is admitted whatever its size; a producer
#: keeps each notice under its own cap (product-core's is 16 KiB with the line).
NOTICE_DRAIN_BUDGET = 64 * 1024

#: The prefix of every placed notice's entry id. The store mints 8- or 32-hex ids
#: with no prefix, so a derived id cannot collide with one it minted.
NOTICE_ENTRY_PREFIX = "nt-"

#: The ``details`` key the kernel writes the notice id under. A ``custom_message``
#: whose details carry it is a placed notice, wherever it sits in the file.
NOTICE_ID_KEY = "notice_id"

NoticeChange = Literal["queued", "placed", "released", "idle", "held"]
"""Why the inbox's listeners are called: a notice was posted, one was placed,
turns were released (``hold_turns(None)``), the harness went idle, or one was
held because its id was taken by something else."""


def notice_entry_id(notice_id: str) -> str:
    """The entry id a notice is placed under: ``nt-`` and 12 hex of its sha256."""

    return NOTICE_ENTRY_PREFIX + hashlib.sha256(notice_id.encode("utf-8")).hexdigest()[:12]


def cut_utf8(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` UTF-8 bytes, at a character boundary."""

    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: max(0, limit)].decode("utf-8", errors="ignore")


def is_notice_details(details: Any) -> bool:
    """Whether a ``custom_message``'s ``details`` mark it as a placed notice."""

    return isinstance(details, Mapping) and isinstance(details.get(NOTICE_ID_KEY), str)


@dataclass(frozen=True)
class Notice:
    """One message a producer leaves for the model.

    ``body`` is the whole text the model reads, already framed and bounded by
    its producer. ``details`` go on the entry with :data:`NOTICE_ID_KEY` added.
    ``brief`` is a one-line summary a surface may show. ``finalize`` is asked,
    once per placement attempt, for a line added to the body at the moment it is
    placed (a hint that depends on the branch then current); a raise is logged
    and adds nothing, a cancel propagates. The line is cut so that it and its
    newline take at most ``finalize_max`` UTF-8 bytes, and a drain charges the
    notice that much beyond its body before it is placed; with
    ``finalize_max`` 0 no line is asked for.
    """

    notice_id: str
    custom_type: str
    body: str
    details: Mapping[str, Any] = field(default_factory=dict)
    brief: str = ""
    finalize: Callable[[], Awaitable[str | None]] | None = None
    finalize_max: int = 0


@dataclass(frozen=True)
class PromptNoticesResult:
    """What :meth:`AgentHarness.prompt_notices` did.

    ``started`` is whether a turn ran. ``placed`` counts the notices placed for
    it before the run began (more may be placed at its boundaries). ``reason``
    says why nothing ran: ``held``, ``disposed``, ``busy``, ``nothing`` (no
    notice pending and none unanswered at the leaf) or ``refused`` (the model
    cannot run).
    """

    started: bool
    placed: int = 0
    reason: str | None = None


@dataclass
class _Posted:
    notice: Notice
    on_placed: Callable[[Notice, str], None] | None
    size: int


class NoticeInbox:
    """The pending, claimed and held notices of one harness, in arrival order.

    Synchronous bookkeeping only: the placement itself (a session write) is the
    harness's, which claims, places and then marks or releases here. Every
    method runs without an ``await``, so a claim is atomic on the event loop.
    """

    def __init__(self) -> None:
        self._pending: list[_Posted] = []
        self._claimed: list[_Posted] = []
        self._held: dict[str, tuple[Notice, str]] = {}
        self._placed: dict[str, str] = {}
        self._listeners: list[Callable[[NoticeChange], None]] = []
        self._changed: asyncio.Event | None = None
        self.placed_count = 0

    # --- reading ---------------------------------------------------------

    @property
    def pending(self) -> int:
        return len(self._pending)

    def undelivered(self) -> list[Notice]:
        """Pending, claimed and held notices, the claimed ones first."""

        return [
            *(p.notice for p in self._claimed),
            *(p.notice for p in self._pending),
            *(notice for notice, _ in self._held.values()),
        ]

    def held(self) -> dict[str, str]:
        """``notice_id`` → why it is held (``placement_conflict``)."""

        return {key: reason for key, (_notice, reason) in self._held.items()}

    def knows(self, notice_id: str) -> bool:
        return (
            notice_id in self._placed
            or notice_id in self._held
            or any(p.notice.notice_id == notice_id for p in (*self._pending, *self._claimed))
        )

    # --- posting and listening -------------------------------------------

    def post(self, notice: Notice, on_placed: Callable[[Notice, str], None] | None) -> bool:
        """Queue ``notice``; ``False`` when its id is already known here."""

        if self.knows(notice.notice_id):
            return False
        size = len(notice.body.encode("utf-8"))
        if notice.finalize is not None:
            size += max(0, notice.finalize_max)
        self._pending.append(_Posted(notice, on_placed, size))
        self.notify("queued")
        return True

    def add_listener(self, listener: Callable[[NoticeChange], None]) -> Callable[[], None]:
        """Call ``listener`` on every change; returns the unsubscribe.

        Called synchronously, from whatever task made the change: a listener
        must not await or raise (a raise is logged and dropped).
        """

        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def notify(self, change: NoticeChange) -> None:
        event, self._changed = self._changed, None
        if event is not None:
            event.set()
        for listener in list(self._listeners):
            try:
                listener(change)
            except Exception:  # noqa: BLE001 — a listener never breaks the inbox
                _log.debug("notice listener raised on %s", change, exc_info=True)

    async def changed(self) -> None:
        """Return at the next :meth:`notify`."""

        if self._changed is None:
            self._changed = asyncio.Event()
        await self._changed.wait()

    # --- claiming --------------------------------------------------------

    def claim(self, budget: int) -> list[_Posted]:
        """Take pending notices in arrival order up to ``budget`` bytes, at least one."""

        taken: list[_Posted] = []
        used = 0
        while self._pending:
            head = self._pending[0]
            if taken and used + head.size > budget:
                break
            taken.append(self._pending.pop(0))
            used += head.size
        self._claimed.extend(taken)
        return taken

    def release(self, posted: list[_Posted]) -> None:
        """Put claimed notices back at the front, in their order: they go next time."""

        back = [p for p in posted if p in self._claimed]
        for p in back:
            self._claimed.remove(p)
        self._pending[:0] = back

    def mark_placed(self, posted: _Posted, entry_id: str) -> None:
        if posted in self._claimed:
            self._claimed.remove(posted)
        self._placed[posted.notice.notice_id] = entry_id
        self.placed_count += 1
        if posted.on_placed is not None:
            try:
                posted.on_placed(posted.notice, entry_id)
            except Exception:  # noqa: BLE001 — the producer's callback never undoes a placement
                _log.warning("a notice's on_placed callback raised", exc_info=True)
        self.notify("placed")

    def hold(self, posted: _Posted, reason: str) -> None:
        """Keep a claimed notice out of every drain: ``reason`` says why."""

        if posted in self._claimed:
            self._claimed.remove(posted)
        self._held[posted.notice.notice_id] = (posted.notice, reason)
        _log.warning("notice %s held: %s", posted.notice.notice_id, reason)
        self.notify("held")


__all__ = [
    "NOTICE_DRAIN_BUDGET",
    "NOTICE_ENTRY_PREFIX",
    "NOTICE_ID_KEY",
    "Notice",
    "NoticeChange",
    "NoticeInbox",
    "PromptNoticesResult",
    "cut_utf8",
    "is_notice_details",
    "notice_entry_id",
]
