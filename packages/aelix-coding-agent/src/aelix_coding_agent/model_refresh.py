"""When a model catalog refresh runs, and who waits for it (#434, ADR-0264 §4.6).

Pi parity: ``packages/coding-agent/src/modes/interactive/model-catalog-refresh.ts``
(the coordinator): concurrent triggers share one run per registry, each caller
keeps its own cancellation, and the last caller to leave aborts the run.
Cancellation belongs to the trigger (pi ``fed6009cc``): every trigger bounds its
own wait at :data:`TRIGGER_TIMEOUT` and the registry has no timer.

The triggers (§4.6) call :func:`refresh_catalogs` (and wait) or
:func:`start_background_refresh` (and do not). Neither runs while the process
is offline (``--offline``, ``AELIX_OFFLINE``, ``PI_OFFLINE``), and a start-up
trigger never runs in a delegated child (``AELIX_SUBAGENT_DEPTH`` of 1 or more),
which reads the cache its parent wrote. With the global ``checkModelCatalog``
off (#609) only ``aelix update --models`` runs: the automatic triggers ask
:func:`auto_refresh_allowed`, the command :func:`refresh_allowed` alone. The
refresher holds its tasks with strong references; event-loop teardown cancels a
run before it publishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.models_store import AbortSignal, ModelsRefreshResult

if TYPE_CHECKING:
    from .model_registry import ModelRegistry

__all__ = [
    "TRIGGER_TIMEOUT",
    "ModelCatalogRefresher",
    "auto_refresh_allowed",
    "bind_catalog_events",
    "refresh_allowed",
    "refresh_catalogs",
    "refresher",
    "reset_refresher",
    "start_background_refresh",
    "startup_refresh_allowed",
]

TRIGGER_TIMEOUT = 15.0
"""How long a trigger waits, in seconds (pi's start-up bound, §4.6)."""


def refresh_allowed() -> bool:
    """False while the process is offline, or with every source off: then no trigger refreshes (§4.6).

    The hosted source is off with ``AELIX_MODEL_CATALOG_URL=`` (empty) or a
    URL it refuses; nothing then says "Refreshing model catalogs…".
    """

    from .hosted_catalog_source import catalog_base_url
    from .util.offline import is_offline

    return not is_offline() and catalog_base_url() is not None


def auto_refresh_allowed(settings_manager: Any = None) -> bool:
    """An AUTOMATIC trigger's gate: :func:`refresh_allowed` and the ``checkModelCatalog`` setting.

    #609: with the setting off, only ``aelix update --models`` checks (it asks
    :func:`refresh_allowed` alone); the start-up, picker, ``/model`` miss and
    ``/login`` triggers ask this. Read at each trigger, so a ``/settings``
    flip applies from the next one. No settings manager (an embedder) reads as on.
    """

    if not refresh_allowed():
        return False
    if settings_manager is None or not hasattr(settings_manager, "get_check_model_catalog"):
        return True
    try:
        return bool(settings_manager.get_check_model_catalog())
    except Exception:  # noqa: BLE001 - an unreadable setting is the default
        return True


def startup_refresh_allowed(settings_manager: Any = None) -> bool:
    """A start-up trigger also skips a delegated child, which reads its parent's cache."""

    from .subagent_contract import subagent_depth

    return auto_refresh_allowed(settings_manager) and subagent_depth() < 1


@dataclass
class _Run:
    task: asyncio.Task[ModelsRefreshResult]
    signal: AbortSignal
    force: bool
    waiters: int = 0


@dataclass
class ModelCatalogRefresher:
    """Shares one run per registry between concurrent triggers (pi's coordinator)."""

    _active: weakref.WeakKeyDictionary[Any, _Run] = field(default_factory=weakref.WeakKeyDictionary)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def _start(self, registry: ModelRegistry, *, trigger: str, force: bool) -> _Run:
        run = self._active.get(registry)
        live = run is not None and not run.task.done() and not run.signal.aborted
        if run is not None and live and (run.force or not force):
            return run
        if run is not None and live:
            # A forced trigger supersedes an unforced run: the registry's newer
            # run refuses the older one's publication.
            run.signal.abort("superseded")
        signal = AbortSignal()
        task = asyncio.ensure_future(
            registry.refresh_models(allow_network=True, force=force, signal=signal, trigger=trigger)
        )
        run = _Run(task, signal, force)
        self._active[registry] = run
        self._hold(task)

        registry_ref = weakref.ref(registry)

        def _done(finished: asyncio.Task[Any]) -> None:
            owner = registry_ref()
            if owner is not None and self._active.get(owner) is run:
                del self._active[owner]
            if not finished.cancelled():
                finished.exception()  # retrieved, never logged as unhandled

        task.add_done_callback(_done)
        return run

    def _hold(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def refresh(
        self,
        registry: ModelRegistry,
        *,
        trigger: str,
        force: bool = False,
        timeout: float = TRIGGER_TIMEOUT,
    ) -> ModelsRefreshResult | None:
        """Join (or start) the registry's run and wait for it; ``None`` on timeout.

        Cancelling the caller leaves the run to the other waiters; when the last
        one leaves (cancelled or timed out) before it finishes, it is aborted.
        Offline, nothing runs and the result is ``None``.
        """

        if not refresh_allowed():
            return None
        run = self._start(registry, trigger=trigger, force=force)
        run.waiters += 1
        try:
            return await asyncio.wait_for(asyncio.shield(run.task), timeout)
        except TimeoutError:
            return None
        except asyncio.CancelledError:
            # The shared run was cancelled (superseded, or torn down), not this
            # caller: the caller gets no result rather than a cancellation.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return None
        finally:
            run.waiters -= 1
            if run.waiters == 0 and not run.task.done():
                run.signal.abort("the last waiter left")
                run.task.cancel()

    def background(
        self,
        registry: ModelRegistry,
        *,
        trigger: str,
        force: bool = False,
        on_done: Callable[[ModelsRefreshResult | None], Any] | None = None,
        timeout: float = TRIGGER_TIMEOUT,
    ) -> asyncio.Task[Any] | None:
        """:meth:`refresh` in a held task; ``on_done`` gets its result. The caller does not wait."""

        if not refresh_allowed():
            return None

        async def _run() -> None:
            result = await self.refresh(registry, trigger=trigger, force=force, timeout=timeout)
            if on_done is not None:
                returned = on_done(result)
                if asyncio.iscoroutine(returned):
                    await returned

        task = asyncio.ensure_future(_run())
        self._hold(task)

        def _quiet(finished: asyncio.Task[Any]) -> None:
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(_quiet)
        return task

    async def cancel_all(self) -> None:
        """Cancel every held task and wait for them (shell and RPC teardown)."""

        tasks = list(self._tasks)
        for run in list(self._active.values()):
            run.signal.abort("shutting down")
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task

    def reset(self) -> None:
        """Forget every run without awaiting (the test fixture's reset)."""

        for run in list(self._active.values()):
            run.signal.abort("reset")
        for task in list(self._tasks):
            if not task.done():
                with contextlib.suppress(RuntimeError):
                    task.cancel()
        self._active = weakref.WeakKeyDictionary()
        self._tasks = set()


_REFRESHER = ModelCatalogRefresher()


def refresher() -> ModelCatalogRefresher:
    """The process's refresher."""

    return _REFRESHER


def reset_refresher() -> None:
    _REFRESHER.reset()


async def refresh_catalogs(
    registry: ModelRegistry,
    *,
    trigger: str,
    force: bool = False,
    timeout: float = TRIGGER_TIMEOUT,
) -> ModelsRefreshResult | None:
    """Wait (at most ``timeout``) for the registry's shared run; ``None`` offline or on timeout."""

    return await _REFRESHER.refresh(registry, trigger=trigger, force=force, timeout=timeout)


def bind_catalog_events(
    registry: ModelRegistry | None, current_harness: Callable[[], Any]
) -> Callable[[], None]:
    """Emit ``model_catalog_refresh`` on the live harness's bus for every run (§4.7).

    ``current_harness`` is read at each emit, so a hot-swapped harness (``/new``,
    ``/resume``, ``/reload``) gets the next event without a rebind. Returns the
    unsubscribe. A handler's failure never fails a refresh (the registry
    swallows it) and a handler cannot change a list.
    """

    if registry is None or not hasattr(registry, "add_refresh_listener"):
        return lambda: None
    from aelix_agent_core.harness.hooks import ModelCatalogRefreshHookEvent

    async def _emit(outcome: Any, trigger: str) -> None:
        harness = current_harness()
        hooks = getattr(harness, "hooks", None)
        if hooks is None:
            return
        await hooks.emit(
            ModelCatalogRefreshHookEvent(
                source=outcome.source,
                trigger=trigger,
                outcome=outcome.outcome,
                revision=outcome.revision,
                added=outcome.added,
                changed=outcome.changed,
                removed=outcome.removed,
                dropped=outcome.dropped,
                error=outcome.error,
                checked_at=outcome.checked_at,
            )
        )

    return registry.add_refresh_listener(_emit)


def start_background_refresh(
    registry: ModelRegistry,
    *,
    trigger: str,
    on_done: Callable[[ModelsRefreshResult | None], Any] | None = None,
    settings_manager: Any = None,
) -> asyncio.Task[Any] | None:
    """A start-up refresh: background, bounded, skipped offline, in a delegated child
    and with ``checkModelCatalog`` off (#609)."""

    if not startup_refresh_allowed(settings_manager):
        return None
    return _REFRESHER.background(registry, trigger=trigger, on_done=on_done)
