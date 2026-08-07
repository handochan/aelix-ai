"""Resolve an explicit ``/model <argument>`` against the LIVE model registry (#134).

``cli.runtime_bootstrap.resolve_model`` resolves a model for LAUNCH, where the
environment is the strongest signal available: with ``OPENROUTER_API_KEY`` set
and no explicit ``--provider`` it treats ANY id as an OpenRouter id and returns
``openrouter/<id>`` on the OpenRouter host, falling back to a bare model for ids
its catalog never saw. That is right for a flag parsed before any registry
exists — and wrong for ``/model <id>`` typed INSIDE a session, where it silently
re-stamps the environment's provider onto an id that provider does not serve:
the switch reports success, the footer updates, the pair is persisted as the
default, and the only symptom is the provider's ``400 … is not a valid model
ID`` on the NEXT send (#134).

This module is the in-session counterpart. It resolves a BARE id the way the
``/model`` picker does — against ``ModelRegistry`` narrowed by configured auth
and the ``/scoped-models`` allow-list — so the result is a properly scoped
``(provider, id)`` pair carrying that provider's real ``api``/``base_url``, or an
explicit refusal. It never guesses across providers.

Scope, deliberately narrow:

* An argument containing ``/`` is left to :func:`resolve_model` unchanged. Under
  an ``OPENROUTER_API_KEY`` the slash form is how OpenRouter's own canonical ids
  are written (``anthropic/claude-sonnet-4-6``), and re-reading it as
  ``provider=anthropic`` would silently move the turn — and the credentials —
  to a different vendor. Only 1 of the 266 bundled OpenRouter models has a
  slash-free id (``auto``), so routing bare ids through the registry costs
  OpenRouter users effectively nothing.
* No registry, an empty registry, or an introspection failure returns UNDECIDED
  so the caller keeps its previous behaviour (headless tests, RPC, test doubles).
  Resolution must never be the thing that breaks a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_ai.streaming import Model


@dataclass(frozen=True)
class ArgumentResolution:
    """Outcome of resolving one ``/model <argument>``.

    Exactly one of three states:

    * ``model`` set — a scoped ``(provider, id)`` the caller should switch to;
    * ``error`` set — an actionable refusal to commit; the caller must NOT
      switch, persist, or print success;
    * both :data:`None` (UNDECIDED) — this module has no opinion; the caller
      falls back to :func:`aelix_coding_agent.cli.runtime_bootstrap.resolve_model`.
    """

    model: Model | None = None
    error: str | None = None


_UNDECIDED = ArgumentResolution()


def _candidates(pool: list[Any], model_id: str) -> list[Any]:
    """Every model in ``pool`` whose bare id equals ``model_id`` (case-folded).

    Case-insensitive to match :func:`find_exact_model_reference_match`, so the
    diagnosis below sees the same set that resolution just failed to collapse.
    """

    lowered = model_id.lower()
    return [m for m in pool if (getattr(m, "id", "") or "").lower() == lowered]


async def resolve_model_argument(
    argument: str,
    *,
    registry: Any,
    current_model: Any = None,
    settings_manager: Any = None,
    warn: Callable[[str], None] | None = None,
) -> ArgumentResolution:
    """Resolve a bare ``/model`` argument to a scoped model, or refuse.

    :param argument: the raw argument (``/model claude-haiku-4-5`` → the id).
    :param registry: the live :class:`~aelix_coding_agent.model_registry.ModelRegistry`
        (duck-typed: ``get_all`` / ``get_available``); :data:`None` → UNDECIDED.
    :param current_model: the session's current model, used ONLY as the
        tie-break below.
    :param settings_manager: threaded into
        :func:`~aelix_coding_agent.core.scoped_models_filter.scoped_available` so
        a ``/scoped-models`` allow-list narrows this the same way it narrows the
        picker.
    :param warn: one-line sink for the allow-list empty-match warning.

    The offered pool mirrors ``run_model_picker``: auth-filtered
    (``get_available``) → allow-list → :func:`partition_runnable`. Matching is
    pi's own :func:`find_exact_model_reference_match`. When that returns
    :data:`None` because SEVERAL providers serve the id, one tie-break applies —
    the provider the session is already on wins, since "switch model" inside a
    github-copilot session means "switch within my seat", not "move vendors".
    Any remaining ambiguity is reported with the candidates listed, never
    guessed: the bundled catalog alone serves ``gpt-5.4`` from six providers, and
    picking one would send that provider's credentials to whichever sorted first.
    """

    model_id = argument.strip()
    # An explicit ``<provider>/<id>`` keeps its launch-path meaning (see module
    # docstring) — including OpenRouter's vendor-slashed canonical ids.
    if not model_id or "/" in model_id or registry is None:
        return _UNDECIDED

    try:
        known = list(registry.get_all())
    except Exception:  # noqa: BLE001 — introspection must never break /model
        return _UNDECIDED
    # An empty registry cannot prove an id wrong (a stub, or a registry that
    # failed to load) — stay out of the way rather than refuse every id.
    if not known:
        return _UNDECIDED

    try:
        from aelix_coding_agent.core.scoped_models_filter import scoped_available

        pool = list(await scoped_available(registry, settings_manager, warn=warn))
    except Exception:  # noqa: BLE001 — a settings/registry read must never lock us out
        try:
            pool = list(registry.get_available())
        except Exception:  # noqa: BLE001
            pool = list(known)

    from aelix_coding_agent.core.runnable_models import partition_runnable

    runnable, _blocked = partition_runnable(pool)
    # Never over-filter to nothing: an all-blocked pool still produces a better
    # message from the diagnosis below than a bare "no such model".
    pool = runnable or pool

    from aelix_coding_agent.core.model_resolver import find_exact_model_reference_match

    match = find_exact_model_reference_match(model_id, pool)
    if match is not None:
        return ArgumentResolution(model=match)

    candidates = _candidates(pool, model_id)
    current_provider = getattr(current_model, "provider", None) or ""
    if current_provider:
        on_current = [m for m in candidates if m.provider == current_provider]
        if len(on_current) == 1:
            return ArgumentResolution(model=on_current[0])
    if candidates:
        listed = ", ".join(sorted({f"{m.provider}/{m.id}" for m in candidates}))
        return ArgumentResolution(
            error=(
                f"model '{model_id}' is served by several providers — name one: "
                f"{listed}"
            )
        )

    # Not offered. Distinguish "the registry has never heard of this id" from
    # "it exists, but not for you right now": telling a logged-out user their id
    # is unknown sends them to fix the wrong thing.
    elsewhere = sorted(
        {
            getattr(m, "provider", "") or "?"
            for m in _candidates(known, model_id)
        }
    )
    where = f" for provider '{current_provider}'" if current_provider else ""
    if elsewhere:
        return ArgumentResolution(
            error=(
                f"no model '{model_id}'{where} — it is served by "
                f"{', '.join(elsewhere)}, which this session has no configured "
                "credentials for (or which is filtered out by /scoped-models). "
                "Run /login, or use <provider>/<id>."
            )
        )
    return ArgumentResolution(
        error=(
            f"no model '{model_id}'{where} — run /model with no argument to pick "
            "from the available models, or use <provider>/<id>."
        )
    )


__all__ = ["ArgumentResolution", "resolve_model_argument"]
