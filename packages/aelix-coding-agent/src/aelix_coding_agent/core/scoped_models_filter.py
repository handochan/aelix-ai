"""enabled_models enforcement helper (WP-2 follow-up, ADR-0162).

WP-2 (ADR-0161) shipped ``/scoped-models``, which PERSISTS an
``enabled_models`` allow-list via
:meth:`aelix_ai.settings.SettingsManager.set_enabled_models`
(``None`` = the canonical "all enabled" sentinel). Nothing consumed it, so
the allow-list was inert. THIS module is the single place the allow-list is
applied: :func:`scoped_available` intersects
:meth:`aelix_coding_agent.model_registry.ModelRegistry.get_available` (the
auth-only, pi-parity catalog) with the live allow-list.

Design (see ADR-0162):

* :meth:`ModelRegistry.get_available` is NEVER modified — it stays auth-only
  (pi parity) and is also the data source
  :func:`aelix_coding_agent.core.model_resolver.resolve_model_scope` reads, so
  scoping it would be circular. The scope lives ONLY here, at the consumers.
* The allow-list is read LIVE on every call
  (``settings_manager.get_enabled_models()``), never snapshotted at startup —
  so a runtime ``/scoped-models`` change is reflected by the very next
  ``/model`` open / ``--list-models`` call within the same process.
* Empty-match GUARD: a concrete (non-empty) allow-list whose patterns match
  ZERO available models degrades to the FULL list (never locks the user out)
  and surfaces a one-line warning via the caller-supplied ``warn`` sink.
* Order is RE-PROJECTED onto ``get_available()`` insertion order (not
  ``resolve_model_scope``'s pattern order) so the picker ``✱`` marker and any
  cycle rotation keep canonical order.

Chain (strictly one-directional, no cycle):
``consumer → scoped_available → resolve_model_scope → get_available() (unscoped)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_ai.streaming import Model

    from ..model_registry import ModelRegistry


async def scoped_available(
    registry: ModelRegistry,
    settings_manager: Any,
    *,
    warn: Callable[[str], None] | None = None,
) -> list[Model]:
    """Return ``registry.get_available()`` narrowed by the live allow-list.

    :param registry: a :class:`ModelRegistry` (must expose
        ``get_available() -> list[Model]``).
    :param settings_manager: the held :class:`SettingsManager` (read LIVE via
        ``get_enabled_models()``); :data:`None` defensively returns the full
        list.
    :param warn: optional one-line sink invoked when a concrete allow-list
        matches zero available models (lockout guard).

    Behaviour:

    * ``settings_manager is None`` OR ``get_enabled_models() is None`` (the
      "all enabled" sentinel) OR ``[]`` (empty list) → the full
      ``get_available()`` unchanged.
    * a concrete non-empty allow-list → :func:`resolve_model_scope` matches it
      against ``get_available()``; the matched ``Model`` set is projected back
      onto ``get_available()`` insertion order (preserves canonical order,
      dedups).
    * a concrete non-empty allow-list matching ZERO available models → the
      full ``get_available()`` plus a ``warn(...)`` (no lockout).
    """

    full = list(registry.get_available())

    if settings_manager is None:
        return full

    try:
        patterns = settings_manager.get_enabled_models()
    except Exception:  # noqa: BLE001 — a settings read must never lock the user out
        return full

    # None (sentinel) or [] (empty list) → all enabled, no scoping.
    if not patterns:
        return full

    # Concrete allow-list → resolve against the UNSCOPED auth list. The scope
    # lives only here; resolve_model_scope re-enters get_available() WITHOUT a
    # scope (one-directional chain, no recursion).
    from .model_resolver import resolve_model_scope

    scoped = await resolve_model_scope(patterns, registry)

    if not scoped:
        # Empty-match guard: a concrete list matched nothing available — do NOT
        # lock the user out. Show all + warn.
        if warn is not None:
            warn(
                "scoped-models allow-list matched no available models "
                "— showing all"
            )
        return full

    # RE-PROJECT onto get_available() order (resolve_model_scope returns models
    # in PATTERN order; the picker ✱ marker + cycle rotation assume canonical
    # insertion order). Identity is (provider, id) — the same key the picker /
    # set_model paths use to recover a Model.
    matched_keys = {(sm.model.provider, sm.model.id) for sm in scoped}
    return [m for m in full if (m.provider, m.id) in matched_keys]


__all__ = ["scoped_available"]
