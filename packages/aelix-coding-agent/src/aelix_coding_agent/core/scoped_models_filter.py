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
  (pi parity). The scope lives ONLY here, at the consumers.
* The allow-list is read LIVE on every call
  (``settings_manager.get_enabled_models()``), never snapshotted at startup —
  so a runtime ``/scoped-models`` change is reflected by the very next
  ``/model`` open / ``--list-models`` call within the same process.
* Identity is (provider, id): a pattern is matched by :func:`_pattern_matches`,
  which accepts a canonical ``provider/id`` (provider-scoped), a legacy bare id
  (back-compat: matches every provider exposing it), or a glob. We do NOT route
  the allow-list through :func:`aelix_coding_agent.core.model_resolver.resolve_model_scope`
  — its bare-id path collapses an id shared across providers to ONE arbitrary
  model, which would silently drop provider-distinct siblings from ``/model``.
* Empty-match GUARD: a concrete (non-empty) allow-list whose patterns match
  ZERO available models degrades to the FULL list (never locks the user out)
  and surfaces a one-line warning via the caller-supplied ``warn`` sink.
* Result preserves ``get_available()`` insertion order (we iterate it directly)
  so the picker ``✱`` marker and any cycle rotation keep canonical order.

Chain (strictly one-directional, no cycle):
``consumer → scoped_available → get_available() (unscoped) ∩ _pattern_matches``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_ai.streaming import Model

    from ..model_registry import ModelRegistry


def _pattern_matches(pattern: str, model: Any) -> bool:
    """Return :data:`True` iff ``pattern`` selects ``model`` under (provider, id).

    Three allow-list pattern forms are honoured — matching the identity used by
    the ``/scoped-models`` picker, :func:`aelix_ai.models.models_are_equal`, and
    the final intersect in :func:`scoped_available`:

    * canonical ``provider/id`` — exact, provider-scoped (the form the picker now
      persists; enabling ``openai/gpt-4o`` no longer touches ``copilot/gpt-4o``);
    * a bare ``id`` — LEGACY back-compat: matches EVERY provider exposing that id,
      so an allow-list written before provider-qualification keeps working (and is
      re-persisted in canonical form the next time ``/scoped-models`` is saved);
    * a glob (``*`` / ``?`` / ``[``) — matched pi-minimatch-style against BOTH
      ``provider/id`` and the bare id (``*`` never crosses ``/``), preserving the
      pi ``enabledModels`` glob semantics power users may hand-edit into settings.
    """

    p = pattern.strip()
    if not p:
        return False
    # Strip a trailing pi thinking-level suffix (e.g. "provider/*:high", "id:high")
    # so a hand-edited enabled-models entry keeps scoping — resolve_model_scope did
    # this, and without it the whole entry matches nothing and the empty-match guard
    # would silently WIDEN scope to the full model list.
    colon_idx = p.rfind(":")
    if colon_idx != -1:
        from .defaults import is_valid_thinking_level

        if is_valid_thinking_level(p[colon_idx + 1 :]):
            p = p[:colon_idx].strip()
    if not p:
        return False

    provider = getattr(model, "provider", None) or ""
    model_id = getattr(model, "id", None) or ""
    canonical = f"{provider}/{model_id}"
    has_slash = "/" in p

    if "*" in p or "?" in p or "[" in p:
        from .model_resolver import _glob_match_pi_minimatch

        # A glob WITH a slash targets provider/id; a slash-free glob targets the
        # bare id (pi minimatch: ``*`` never crosses ``/``).
        if has_slash:
            return _glob_match_pi_minimatch(canonical, p)
        return _glob_match_pi_minimatch(canonical, p) or _glob_match_pi_minimatch(
            model_id, p
        )

    pl = p.lower()
    if has_slash:
        # A slash makes this a CANONICAL provider/id — match the canonical key ONLY.
        # Falling back to the bare id would leak into a DIFFERENT provider whose
        # model id literally contains a slash (e.g. openrouter's "openai/gpt-4o",
        # whose canonical is "openrouter/openai/gpt-4o"), re-opening the S3 collision.
        # (A pre-qualification legacy slashed bare id is therefore reinterpreted as a
        # canonical key — acceptable, since new /scoped-models saves are canonical.)
        return pl == canonical.lower()
    # Slash-free → a legacy BARE id: matches every provider exposing that id.
    return pl == model_id.lower()


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
    * a concrete non-empty allow-list → each model from ``get_available()`` is
      tested against the patterns via :func:`_pattern_matches` (canonical
      ``provider/id`` exact, legacy bare-id back-compat, glob, ``:level`` suffix
      tolerated); iterating ``get_available()`` directly preserves canonical
      insertion order and yields one entry per (provider, id).
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

    # Concrete allow-list → match each available model against the patterns using
    # (provider, id) IDENTITY (not the fuzzy single-model resolve_model_scope path,
    # which collapses an ambiguous BARE id like ``gpt-4o`` — shared by 4 providers
    # in the catalog — down to ONE arbitrary model and silently drops the rest).
    # :func:`_pattern_matches` honours canonical ``provider/id``, a legacy bare id
    # (matches every provider exposing it, for back-compat with allow-lists written
    # before provider-qualification), and globs. Iterating ``full`` preserves
    # get_available() insertion order (the picker ✱ marker + cycle rotation assume
    # it) and yields one entry per (provider, id) with no duplicates.
    matched = [m for m in full if any(_pattern_matches(p, m) for p in patterns)]

    if not matched:
        # Empty-match guard: a concrete list matched nothing available — do NOT
        # lock the user out. Show all + warn.
        if warn is not None:
            warn(
                "scoped-models allow-list matched no available models "
                "— showing all"
            )
        return full

    return matched


__all__ = ["scoped_available"]
