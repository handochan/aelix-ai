"""The running foreground calls Ctrl+B could move into a job: kind-agnostic offers (#775, ADR-0298).

A client whose call can be moved — the ``agent`` kind's TUI ``run`` or
``resume`` — makes an OFFER while its child is live and takes it back when that
window ends. The host that owns the key (the TUI's ``JobsUi``) reads the offers
and installs the GUARD every commit asks: while it answers a reason — an
interrupt or an abort is pending, a session change is running — no call moves.
Without a host there is no guard, and every commit is refused.

Nothing here names a kind, and nothing here moves anything: the client's own
commit does that (``aelix_agents.detach``), through the job service's
``prepare_adopted`` / ``adopt``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Protocol

_log = logging.getLogger(__name__)

NO_HOST = "no interactive host can move it"


class DetachOffer(Protocol):
    def ready(self) -> bool:
        """Whether a key press now would be taken."""
        ...

    def request(self) -> bool:
        """The key: wake the call; whether it was taken."""
        ...


class DetachOffers:
    """The offers of this process, the host's guard, and where a refusal is said."""

    def __init__(self) -> None:
        self._offers: list[DetachOffer] = []
        #: The host's answer to "may a call move now?": a reason, or ``None``.
        self.guard: Callable[[], str | None] | None = None
        #: Where a commit's refusal is said, one line (the TUI's transcript).
        self.note: Callable[[str], None] | None = None

    def unhost(self, guard: Callable[[], str | None]) -> None:
        """The host whose ``guard`` this is has gone: no guard, no note."""

        if self.guard == guard:
            self.guard = None
            self.note = None

    def offer(self, offer: DetachOffer) -> Callable[[], None]:
        """Make ``offer``; the callable that takes it back (idempotent)."""

        self._offers.append(offer)

        def withdraw() -> None:
            with contextlib.suppress(ValueError):
                self._offers.remove(offer)

        return withdraw

    def ready(self) -> list[DetachOffer]:
        """The offers a key press now would be taken by."""

        found: list[DetachOffer] = []
        for offer in list(self._offers):
            try:
                ready = offer.ready()
            except Exception:  # noqa: BLE001 — an offer that cannot answer is not ready
                ready = False
            if ready:
                found.append(offer)
        return found

    def request(self) -> bool:
        """Wake the ONE ready offer; ``False`` with none or several (Qwen's "exactly one")."""

        ready = self.ready()
        if len(ready) != 1:
            return False
        try:
            return bool(ready[0].request())
        except Exception:  # noqa: BLE001
            _log.debug("a detach request raised", exc_info=True)
            return False

    def blocked(self) -> str | None:
        """Why no call may move now, or ``None``; asked by every commit."""

        guard = self.guard
        if guard is None:
            return NO_HOST
        try:
            return guard()
        except Exception:  # noqa: BLE001 — a host that cannot answer refuses
            return "the host could not say whether it is safe"

    def refused(self, why: str) -> None:
        note = self.note
        if note is not None:
            with contextlib.suppress(Exception):
                note(why)


__all__ = ["NO_HOST", "DetachOffer", "DetachOffers"]
