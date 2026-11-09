"""How many jobs of each kind may run at once (#722, ADR-0293 §admission, owner OQ6).

A kind declares a default bound and the settings key that names it; the user
may change it in the GLOBAL settings, ``backgroundJobs.limits.<key>``, and may
set a total across kinds, ``backgroundJobs.maxTotal`` (off by default). A
repository's ``.aelix/settings.json`` cannot raise its own limits: the getter
reads the global file only, as ``get_features_agents`` does.

Every value is read **at each reservation**, so a change applies to the next
one and never stops a running job; a lowered limit refuses new work until the
count falls below it. A value that is not an integer within its hard maximum
falls back to the default, with one warning per distinct bad value.

A :class:`Reservation` is taken in one synchronous block (atomic on the event
loop) and released on the job's **confirmed cleanup**, not when its notice is
sent: a job whose processes are not yet confirmed stopped still holds its slot.

**Counted by kind** (Codex #17). Slots are counted by the kind's registered
name, which stays the same across generations; the settings key only selects
which setting bounds it. So a kind registered again under another settings key
still counts the jobs its earlier generation runs, and two kinds that share a
key do not share a count. A client that reserves before consent hands its
reservation to the start, which takes its slot out of it (:meth:`Reservation.split`)
instead of asking for another.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

_log = logging.getLogger(__name__)

#: The bound no setting can exceed, per kind (ADR-0008 (c) as amended by ADR-0293:
#: an expressly allowed host-safety invariant).
HARD_MAX_PER_KIND = 16
#: The bound no ``maxTotal`` can exceed, across kinds.
HARD_MAX_TOTAL = 32


class AdmissionRefused(Exception):
    """No slot is free. The message names the setting that bounds it."""


class Reservation:
    """Slots held for a kind; :meth:`release` gives them back, once."""

    def __init__(self, owner: Admission, key: str, slots: int) -> None:
        self._owner = owner
        #: The kind the slots are counted under.
        self.key = key
        self.slots = slots
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def belongs_to(self, admission: Admission) -> bool:
        return self._owner is admission

    def split(self, slots: int = 1) -> Reservation:
        """Move ``slots`` of these slots into a new reservation, atomically: the count does not change.

        The client's pre-consent reservation hands a job its slot this way. A
        reservation whose last slot moved out is spent: releasing it gives back
        nothing.
        """

        if self._released or slots < 1 or slots > self.slots:
            raise ValueError(f"cannot take {slots} slot(s) from a reservation of {self.slots}")
        self.slots -= slots
        if self.slots == 0:
            self._released = True
        return Reservation(self._owner, self.key, slots)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._owner._give_back(self.key, self.slots)


class Admission:
    """The per-kind and total counts of one process, and the settings that bound them."""

    def __init__(
        self,
        read_settings: Callable[[], Mapping[str, Any] | None] | None = None,
        *,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._read = read_settings
        self._warn = warn
        self._counts: dict[str, int] = {}
        self._warned: set[str] = set()

    # --- the settings, read now ------------------------------------------

    def _settings(self) -> Mapping[str, Any]:
        if self._read is None:
            return {}
        try:
            value = self._read()
        except Exception:  # noqa: BLE001 — unreadable settings mean defaults
            _log.debug("background-jobs settings unreadable", exc_info=True)
            return {}
        return value if isinstance(value, Mapping) else {}

    def _say_once(self, key: str, text: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        _log.warning("%s", text)
        if self._warn is not None:
            try:
                self._warn(text)
            except Exception:  # noqa: BLE001
                _log.debug("admission warning sink raised", exc_info=True)

    def limit_for(self, key: str, default: int) -> int:
        """The bound for ``key``: the user's setting when valid, else the kind's default, never above the hard maximum."""

        fallback = max(1, min(int(default), HARD_MAX_PER_KIND))
        limits = self._settings().get("limits")
        if not isinstance(limits, Mapping) or key not in limits:
            return fallback
        value = limits[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= HARD_MAX_PER_KIND
        ):
            self._say_once(
                f"limits.{key}={value!r}",
                f"settings backgroundJobs.limits.{key} must be an integer from 1 to "
                f"{HARD_MAX_PER_KIND}; using {fallback}",
            )
            return fallback
        return value

    def total_limit(self) -> int | None:
        """``maxTotal``: ``None`` (off, the default) or an integer from 1 to the hard maximum."""

        settings = self._settings()
        if "maxTotal" not in settings or settings.get("maxTotal") is None:
            return None
        value = settings["maxTotal"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= HARD_MAX_TOTAL
        ):
            self._say_once(
                f"maxTotal={value!r}",
                f"settings backgroundJobs.maxTotal must be null or an integer from 1 to "
                f"{HARD_MAX_TOTAL}; it is off",
            )
            return None
        return value

    # --- counting ----------------------------------------------------------

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)

    def total(self) -> int:
        return sum(self._counts.values())

    def free(self, kind: str, default: int, *, settings_key: str | None = None) -> int:
        """Slots free for ``kind`` now: its bound (from ``settings_key``, default the kind) less its count."""

        key = kind if settings_key is None else settings_key
        kind_free = self.limit_for(key, default) - self.count(kind)
        total = self.total_limit()
        if total is not None:
            kind_free = min(kind_free, total - self.total())
        return max(0, kind_free)

    def reserve(
        self,
        kind: str,
        default: int,
        slots: int = 1,
        *,
        settings_key: str | None = None,
        noun: tuple[str, str] | None = None,
    ) -> Reservation:
        """Exactly ``slots`` counted under ``kind``, or :class:`AdmissionRefused`."""

        if slots < 1:
            raise ValueError("a reservation takes at least one slot")
        if self.free(kind, default, settings_key=settings_key) < slots:
            raise AdmissionRefused(self._refusal(kind, default, settings_key, noun))
        self._counts[kind] = self.count(kind) + slots
        return Reservation(self, kind, slots)

    def reserve_up_to(
        self,
        kind: str,
        default: int,
        wanted: int,
        *,
        settings_key: str | None = None,
        noun: tuple[str, str] | None = None,
    ) -> Reservation:
        """``min(wanted, free)`` slots, at least one, or :class:`AdmissionRefused`.

        For a client that sizes its work to what is free before it asks for
        consent, so that nothing is refused after it (Codex r1 #17); it hands
        the reservation to each start (:meth:`Reservation.split`).
        """

        free = self.free(kind, default, settings_key=settings_key)
        if free < 1:
            raise AdmissionRefused(self._refusal(kind, default, settings_key, noun))
        slots = max(1, min(int(wanted), free))
        self._counts[kind] = self.count(kind) + slots
        return Reservation(self, kind, slots)

    def hold(self, kind: str, slots: int = 1) -> Reservation:
        """Slots for work that already runs (a restart's ``unresolved`` job): counted, never refused."""

        self._counts[kind] = self.count(kind) + slots
        return Reservation(self, kind, slots)

    def _refusal(
        self,
        kind: str,
        default: int,
        settings_key: str | None = None,
        noun: tuple[str, str] | None = None,
    ) -> str:
        """Why nothing is free. ``noun`` is the kind's own ``(one, many)`` for what runs
        (``KindCapabilities.noun``); without one it is "background jobs of this kind"."""

        key = kind if settings_key is None else settings_key
        running = self.count(kind)
        limit = self.limit_for(key, default)
        total = self.total_limit()
        if total is not None and self.total() >= total and running < limit:
            # Counted across every kind, and a kind may count what runs in the
            # foreground too (delegation counts every live agent): no kind's
            # noun, and not "background jobs" (#611 S1c, verify-r2 N5).
            return (
                f"{self.total()} run across every kind, and settings "
                f"backgroundJobs.maxTotal allows {total}. Stop one or wait; do not retry."
            )
        if noun is not None:
            one, many = noun
            what = f"{running} {one} already runs" if running == 1 else f"{running} {many} already run"
        else:
            what = f"{running} background jobs of this kind already run"
        return (
            f"{what}; this limit is set in settings "
            f"backgroundJobs.limits.{key}. Stop one or wait; do not retry."
        )

    def _give_back(self, key: str, slots: int) -> None:
        left = self.count(key) - slots
        if left > 0:
            self._counts[key] = left
        else:
            self._counts.pop(key, None)


__all__ = [
    "HARD_MAX_PER_KIND",
    "HARD_MAX_TOTAL",
    "Admission",
    "AdmissionRefused",
    "Reservation",
]
