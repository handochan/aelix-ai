"""Resolve an explicit ``/model <argument>`` against the LIVE model registry (#134).

``cli.runtime_bootstrap.resolve_model`` resolves a model for LAUNCH, where the
environment is the strongest signal available: with ``OPENROUTER_API_KEY`` set
and no explicit ``--provider`` it treats ANY argument as an OpenRouter id and
returns ``openrouter/<argument>`` on the OpenRouter host, falling back to a bare
model for ids its catalog never saw. That is right for a flag parsed before any
registry exists — and wrong for ``/model <id>`` typed INSIDE a session, where it
silently re-stamps the environment's provider onto an id that provider does not
serve: the switch reports success, the footer updates, the pair is persisted as
the default, and the only symptom is the provider's ``400 … is not a valid model
ID`` on the NEXT send (#134).

This module is the in-session counterpart. It resolves the argument the way the
``/model`` picker does — against ``ModelRegistry`` narrowed by configured auth
and the ``/scoped-models`` allow-list — so the result is a properly scoped
``(provider, id)`` pair carrying that provider's real ``api``/``base_url``, or an
explicit refusal. It never guesses across providers.

BOTH argument forms go through that pool, including ``<provider>/<id>``. An
earlier revision of this module exempted the slash form on the theory that under
an ``OPENROUTER_API_KEY`` it is how OpenRouter's own canonical ids are written.
That exemption preserved #134 verbatim: OpenRouter's Anthropic ids are
DOT-versioned (``anthropic/claude-sonnet-4.5``), so a dash-versioned
``anthropic/claude-haiku-4-5`` missed the OpenRouter catalog and came back as a
fully-formed ``openrouter/anthropic/claude-haiku-4-5`` — moving a session that
was ALREADY on Anthropic, with the user's fully-qualified id, onto OpenRouter's
host and credentials.

:func:`find_exact_model_reference_match` (pi's own resolver) distinguishes the
two namespaces correctly against the real catalog: it tries the canonical
``provider/id`` key first, then a ``provider``/``id`` split, then the whole
string as a bare id. So ``anthropic/claude-haiku-4-5`` finds the Anthropic model,
``openrouter/auto`` finds OpenRouter's ``auto``, and OpenRouter's vendor-slashed
``anthropic/claude-sonnet-4.5`` still resolves as an OpenRouter model when no
Anthropic provider is available to claim it — each with its own base_url.

Degradation: no registry, an empty registry, or a failed introspection returns
UNDECIDED so the caller keeps its previous behaviour (headless, RPC, test
doubles). Resolution must never be the thing that breaks a session.
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


def _candidates(models: list[Any], argument: str) -> list[Any]:
    """Every model in ``models`` that ``argument`` could name, case-folded.

    Mirrors the three readings :func:`find_exact_model_reference_match` tries, so
    the diagnosis below reports the same set that resolution just failed to
    collapse to one: the canonical ``provider/id`` key, a ``provider``/``id``
    split, and the whole argument as a bare id. A vendor-slashed gateway id
    (OpenRouter's ``anthropic/claude-sonnet-4.5``) is found by the third reading
    while a real provider-scoped reference is found by the second — which is why
    both must be tried here rather than only the one that "looks" right.
    """

    lowered = argument.lower()
    provider, _, rest = argument.partition("/")
    provider, rest = provider.lower(), rest.lower()
    found: list[Any] = []
    for model in models:
        model_id = (getattr(model, "id", "") or "").lower()
        model_provider = (getattr(model, "provider", "") or "").lower()
        if (
            model_id == lowered
            or f"{model_provider}/{model_id}" == lowered
            or (rest and model_provider == provider and model_id == rest)
        ):
            found.append(model)
    return found


async def resolve_model_argument(
    argument: str,
    *,
    registry: Any,
    current_model: Any = None,
    settings_manager: Any = None,
    warn: Callable[[str], None] | None = None,
) -> ArgumentResolution:
    """Resolve a ``/model`` argument to a scoped model, or refuse.

    :param argument: the raw argument — a bare id (``claude-haiku-4-5``) or a
        provider-scoped reference (``anthropic/claude-haiku-4-5``).
    :param registry: the live :class:`~aelix_coding_agent.model_registry.ModelRegistry`
        (duck-typed: ``get_all`` / ``get_available``); :data:`None` → UNDECIDED.
    :param current_model: the session's current model, used ONLY as the bare-id
        tie-break below.
    :param settings_manager: threaded into
        :func:`~aelix_coding_agent.core.scoped_models_filter.scoped_available` so
        a ``/scoped-models`` allow-list narrows this the same way it narrows the
        picker.
    :param warn: one-line sink for the allow-list empty-match warning.

    The offered pool mirrors ``run_model_picker``: auth-filtered
    (``get_available``) → allow-list → :func:`partition_runnable`. Matching is
    pi's :func:`find_exact_model_reference_match`.

    When that returns :data:`None` because SEVERAL providers serve the argument,
    one tie-break applies, and only to a BARE id: the provider the session is
    already on wins, since "switch model" inside a github-copilot session means
    "switch within my seat", not "move vendors". A ``<provider>/<id>`` argument
    gets no tie-break — it NAMES a provider, so silently substituting a different
    one is the exact failure this module exists to prevent. Any remaining
    ambiguity is reported with the candidates listed, never guessed: the bundled
    catalog serves ``gpt-5.4`` from six providers and ``anthropic/claude-sonnet-4.5``
    from two, and picking one would send that provider's credentials to whichever
    sorted first.
    """

    reference = argument.strip()
    if not reference or registry is None:
        return _UNDECIDED

    try:
        known = list(registry.get_all())
    except Exception:  # noqa: BLE001 — introspection must never break /model
        return _UNDECIDED
    # An empty registry cannot prove an argument wrong (a stub, or a registry
    # that failed to load) — stay out of the way rather than refuse everything.
    if not known:
        return _UNDECIDED

    try:
        from aelix_coding_agent.core.scoped_models_filter import scoped_available

        pool = list(await scoped_available(registry, settings_manager, warn=warn))
    except Exception:  # noqa: BLE001 — a settings/registry read must never lock us out
        try:
            pool = list(registry.get_available())
        except Exception:  # noqa: BLE001
            # Do NOT fall back to get_all(): it spans providers with no
            # configured credentials, so an id unique among THOSE would resolve
            # and switch, trading a clear refusal for a confusing auth failure.
            # ``known`` stays available for the diagnosis text only.
            return _UNDECIDED

    from aelix_coding_agent.core.runnable_models import partition_runnable

    runnable, _blocked = partition_runnable(pool)
    # Never over-filter to nothing: an all-blocked pool still produces a better
    # message from the diagnosis below than a bare "no such model".
    pool = runnable or pool

    from aelix_coding_agent.core.model_resolver import find_exact_model_reference_match

    match = find_exact_model_reference_match(reference, pool)
    if match is not None:
        return ArgumentResolution(model=match)

    candidates = _candidates(pool, reference)
    current_provider = getattr(current_model, "provider", None) or ""
    if current_provider and "/" not in reference:
        on_current = [m for m in candidates if m.provider == current_provider]
        if len(on_current) == 1:
            return ArgumentResolution(model=on_current[0])
    if candidates:
        listed = ", ".join(sorted({f"{m.provider}/{m.id}" for m in candidates}))
        return ArgumentResolution(
            error=(
                f"model '{reference}' is served by several providers — name one: "
                f"{listed}"
            )
        )

    # Not offered. Distinguish "the registry has never heard of this" from "it
    # exists, but not for you right now": telling a logged-out user their id is
    # unknown sends them to fix the wrong thing.
    elsewhere = sorted(
        {getattr(m, "provider", "") or "?" for m in _candidates(known, reference)}
    )
    if elsewhere:
        return ArgumentResolution(
            error=(
                f"no model '{reference}' is available in this session — it is "
                f"served by {', '.join(elsewhere)}, which this session has no "
                "configured credentials for (or which is filtered out by "
                "/scoped-models). Run /login, or use <provider>/<id>."
            )
        )
    return ArgumentResolution(
        error=(
            f"no model '{reference}' is available in this session — run /model "
            "with no argument to pick from the available models, or use "
            "<provider>/<id>."
        )
    )


__all__ = ["ArgumentResolution", "resolve_model_argument"]
