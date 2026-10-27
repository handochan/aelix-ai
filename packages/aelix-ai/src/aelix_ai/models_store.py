"""The model catalog store and the refresh contract (#434, ADR-0264 §4.3, §4.7).

Pi parity: ``packages/ai/src/models-store.ts`` (``ModelsStore``,
``InMemoryModelsStore``) and the refresh contract of ``packages/ai/src/models.ts``
(``RefreshModelsContext``, ``publish``, ``ModelsRefreshResult``), in aelix's
names. pi keys its store by provider id; aelix keys it by SOURCE: the hosted
catalog (:data:`HOSTED_ENTRY`) and, with #450, OpenRouter's key-scoped list
(:data:`NARROWING_ENTRY`).

What differs from pi, and why:

- The store is synchronous, so :class:`~aelix_coding_agent.model_registry.ModelRegistry`'s
  constructor can read it before its first composition (the cache phase).
- :meth:`ModelsStore.write` compares before it writes. A run passes the stamp
  (:func:`entry_stamp`: ``checkedAt`` and ``etag``) of the entry it started
  from; when another process persisted meanwhile, nothing is written and the
  newer stored entry comes back, so the run publishes that one instead and a
  slow run never writes an older index over a newer one (§4.3 rule 2).
- Cancellation is an :class:`AbortSignal` the trigger owns (pi ``fed6009cc``):
  every trigger passes its own and bounds its own wait, and nothing here has a
  timer.

This module imports nothing above ``aelix_ai``: the agent dir, the offline
answer and the base URL arrive as arguments (§4.7). The file-backed store is
coding-agent's (``aelix_coding_agent.models_store``), where pi keeps it.
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "HOSTED_ENTRY",
    "NARROWING_ENTRY",
    "STORE_VERSION",
    "AbortSignal",
    "InMemoryModelsStore",
    "ModelsPublication",
    "ModelsRefreshResult",
    "ModelsStore",
    "ModelsStoreError",
    "RefreshAborted",
    "RefreshModelsContext",
    "SourceOutcome",
    "entry_stamp",
]

STORE_VERSION = 1
"""``models-store.json``'s ``version``. A file with another one is neither read nor rewritten."""

HOSTED_ENTRY = "aelix-hosted"
"""The hosted catalog's store entry (§4.3 The store)."""

NARROWING_ENTRY = "openrouter"
"""OpenRouter's key-scoped list (#450, §4.5). Nothing in #434 writes it."""

Entry = dict[str, Any]
Stamp = tuple[Any, Any]


def entry_stamp(entry: Mapping[str, Any] | None) -> Stamp | None:
    """What a persist compares: the stored entry's ``checkedAt`` and ``etag`` (§4.3 rule 2).

    ``None`` for no entry. Two runs that started from the same stored entry
    carry the same stamp, and any persist moves ``checkedAt``.
    """

    if entry is None:
        return None
    return (entry.get("checkedAt"), entry.get("etag"))


class ModelsStoreError(Exception):
    """A read-modify-write that did not happen.

    The lock was not taken in time, the agent dir is read-only, Windows
    refused the replace, or the file has a ``version`` this build does not
    know. The run that asked reports ``failed`` and publishes nothing in memory
    (§4.3 rule 2).
    """


class ModelsStore(Protocol):
    """Persistent refresh entries keyed by source (pi ``ModelsStore``, synchronous here).

    An entry is a JSON object. :meth:`read` returns a copy the caller may keep;
    :meth:`write` and :meth:`delete` raise :class:`ModelsStoreError` when the
    store could not be changed.
    """

    def read(self, key: str) -> Entry | None:
        """The stored entry for ``key``, or ``None``."""
        ...

    def write(self, key: str, entry: Mapping[str, Any], *, base: Stamp | None) -> Entry | None:
        """Store ``entry`` unless the stored one moved since ``base`` (:func:`entry_stamp`).

        Returns ``None`` when ``entry`` was written. Returns the stored entry,
        untouched, when its stamp is no longer ``base``: another process
        persisted meanwhile, and that newer entry is what to publish; ``{}``
        when the entry the run started from has since been deleted.
        """
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``'s entry (nothing when there is none)."""
        ...


class InMemoryModelsStore:
    """pi ``InMemoryModelsStore``: a dict of deep copies, for tests and embedders."""

    def __init__(self, entries: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._entries: dict[str, Entry] = {
            key: copy.deepcopy(dict(value)) for key, value in (entries or {}).items()
        }

    def read(self, key: str) -> Entry | None:
        entry = self._entries.get(key)
        return copy.deepcopy(entry) if entry is not None else None

    def write(self, key: str, entry: Mapping[str, Any], *, base: Stamp | None) -> Entry | None:
        current = self._entries.get(key)
        if entry_stamp(current) != base:
            return copy.deepcopy(current) if current is not None else {}
        self._entries[key] = copy.deepcopy(dict(entry))
        return None

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)


class RefreshAborted(Exception):
    """Raised by :meth:`AbortSignal.throw_if_aborted` once the trigger gave up."""


class AbortSignal:
    """The trigger's cancellation (pi's ``AbortSignal``; ownership per pi ``fed6009cc``).

    A plain flag with callbacks, so it can be made outside an event loop. The
    refresher aborts a shared run's signal when its last waiter leaves; a
    source checks it at every await, and :meth:`RefreshModelsContext.publish`
    refuses once it is set.
    """

    def __init__(self) -> None:
        self._aborted = False
        self._reason: str | None = None
        self._callbacks: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    def abort(self, reason: str = "aborted") -> None:
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            # A listener never breaks an abort.
            with contextlib.suppress(Exception):
                callback()

    def on_abort(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` once when aborted (at once if it already is)."""

        if self._aborted:
            callback()
        else:
            self._callbacks.append(callback)

    def throw_if_aborted(self) -> None:
        if self._aborted:
            raise RefreshAborted(self._reason or "aborted")


@dataclass(frozen=True)
class ModelsPublication:
    """What one run publishes (pi ``publish({ persist, update })``).

    ``persist`` is the store entry to write (``None`` writes nothing) and
    ``base`` the stamp of the entry the run started from. ``update`` is what
    memory takes, opaque here: the registry hands it to the source's applier.
    ``on_lost`` turns the newer stored entry into the update to publish instead
    when the persist loses to another process (§4.3 rule 2).
    """

    persist: Mapping[str, Any] | None = None
    base: Stamp | None = None
    update: Any = None
    on_lost: Callable[[Entry], Any] | None = None


@dataclass
class RefreshModelsContext:
    """One run of one source (pi ``RefreshModelsContext``, ``models.ts:80-101``).

    ``stored`` is the source's store entry when the run started. ``allow_network``
    is false whenever the process is offline, so an extension that calls
    ``refresh_models`` honours ``--offline``. ``credential`` is pi's field, for a
    key-scoped source (#450); the hosted source never sends one.
    """

    source: str
    trigger: str
    stored: Entry | None
    allow_network: bool
    force: bool
    signal: AbortSignal
    base_url: str | None = None
    credential: str | None = None
    _publish: Callable[[ModelsPublication], bool] | None = field(default=None, repr=False)

    def publish(self, publication: ModelsPublication) -> bool:
        """Persist, then update memory; ``False`` when the run may no longer publish.

        Generation-checked and serialized per source (§4.3 rule 2): refused once
        the run is superseded or aborted, checked before persisting and again
        before updating memory. A write that fails raises
        :class:`ModelsStoreError` and publishes nothing in memory.
        """

        if self._publish is None:
            return False
        return self._publish(publication)


@dataclass(frozen=True)
class SourceOutcome:
    """What one source's run did, as the ``model_catalog_refresh`` event reports it (§4.7).

    ``outcome`` is one of ``published``, ``unchanged``, ``unavailable``,
    ``withdrawn``, ``not-applicable``, ``failed``, ``aborted`` or ``skipped``.
    ``error`` is already bounded and escaped. No credential and no URL.
    """

    source: str
    outcome: str
    revision: str | None = None
    added: int = 0
    changed: int = 0
    removed: int = 0
    dropped: int = 0
    error: str | None = None
    checked_at: float | None = None


@dataclass(frozen=True)
class ModelsRefreshResult:
    """pi ``ModelsRefreshResult``: ``aborted``, ``errors``, ``published``, ``skipped``.

    ``outcomes`` holds each source's :class:`SourceOutcome`, in run order.
    """

    aborted: bool = False
    errors: tuple[str, ...] = ()
    published: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    outcomes: tuple[SourceOutcome, ...] = ()
