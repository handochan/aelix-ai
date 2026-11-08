"""Kinds by name and version, and the generation of each registration (#722, ADR-0293).

A kind is registered by an extension's ``setup`` (or by product-core for its own
tools), so a ``/reload`` registers it again: a new **generation**, perhaps a new
version. A job keeps running under the runner it started with; its notice is
rendered by the adapter registered *now* when there is one, and from the body
stored with its outcome when there is not.

A record whose kind is not registered, or whose ``kind_version`` is newer than
the registered adapter's, is never interpreted: it stays ``unresolved`` with
``adapter_missing`` or ``adapter_too_old`` and is re-evaluated when that kind
registers (:meth:`KindRegistry.add_listener`). Nothing is made up for it.

**Who may register a name** (Codex #18). A kind's name belongs to the source
that registered it first — an extension's name, or ``""`` for product-core —
for the life of the process: another source registering it is refused, so one
extension cannot take over another's kind and render its results. A
registration made through an extension's API lives as long as that API's
runtime: once a reload or a session change invalidates it, :meth:`KindRegistry.get`
no longer returns it (no new job starts on it) until the extension registers
again. Jobs already running keep the registration they started with.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from aelix_coding_agent.jobs.contract import JobKindAdapter, RunnerFactory

_log = logging.getLogger(__name__)


class KindTaken(ValueError):
    """The kind's name is registered by another source."""


@dataclass(frozen=True)
class Registration:
    adapter: JobKindAdapter
    runner_factory: RunnerFactory
    generation: int
    source: str = ""
    #: Raises once the registering API's runtime is invalidated; ``None`` lives forever.
    lifetime: Callable[[], None] | None = None

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def version(self) -> int:
        return int(self.adapter.version)

    @property
    def alive(self) -> bool:
        if self.lifetime is None:
            return True
        try:
            self.lifetime()
        except Exception:  # noqa: BLE001 — an invalidated runtime raises; anything else too
            return False
        return True


class KindRegistry:
    """The kinds registered in this process, each by its name, latest registration winning."""

    def __init__(self) -> None:
        self._kinds: dict[str, Registration] = {}
        self._owners: dict[str, str] = {}
        self._generation = 0
        self._listeners: list[Callable[[Registration], None]] = []

    def register(
        self,
        adapter: JobKindAdapter,
        runner_factory: RunnerFactory,
        *,
        source: str = "",
        lifetime: Callable[[], None] | None = None,
    ) -> Registration:
        name = adapter.name
        if not isinstance(name, str) or not name:
            raise ValueError("a job kind needs a non-empty name")
        if not isinstance(adapter.version, int) or adapter.version < 1:
            raise ValueError(f"job kind {name!r} needs an integer version >= 1")
        owner = self._owners.setdefault(name, source)
        if owner != source:
            raise KindTaken(
                f"job kind {name!r} is registered by {owner or 'aelix'!r}, not {source or 'aelix'!r}"
            )
        self._generation += 1
        registration = Registration(
            adapter, runner_factory, self._generation, source=source, lifetime=lifetime
        )
        self._kinds[name] = registration
        for listener in list(self._listeners):
            try:
                listener(registration)
            except Exception:  # noqa: BLE001 — a listener never undoes a registration
                _log.warning("a kind-registration listener raised", exc_info=True)
        return registration

    def unregister(self, name: str, generation: int | None = None) -> None:
        current = self._kinds.get(name)
        if current is not None and (generation is None or current.generation == generation):
            del self._kinds[name]

    def get(self, name: str) -> Registration | None:
        """The live registration of ``name``: ``None`` when none, or when its API's runtime ended."""

        registration = self._kinds.get(name)
        return registration if registration is not None and registration.alive else None

    def owner_of(self, name: str) -> str | None:
        """The source that owns ``name``, or ``None`` when it was never registered."""

        return self._owners.get(name)

    def resolve(self, name: str, version: int) -> tuple[Registration | None, str | None]:
        """The registration that may read a record of ``name`` at ``version``, or why none may.

        ``adapter_missing`` when nothing live is registered under the name,
        ``adapter_too_old`` when the registered version is below the record's.
        """

        registration = self.get(name)
        if registration is None:
            return None, "adapter_missing"
        if registration.version < version:
            return None, "adapter_too_old"
        return registration, None

    def names(self) -> list[str]:
        return sorted(self._kinds)

    def add_listener(self, listener: Callable[[Registration], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove


__all__ = ["KindRegistry", "KindTaken", "Registration"]
