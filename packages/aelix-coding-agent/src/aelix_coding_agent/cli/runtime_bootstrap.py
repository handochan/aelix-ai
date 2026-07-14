"""CLI runtime bootstrap — provider registration + .env load + model resolution.

Wires real LLM turns for the interactive / print / rpc CLI. Three pieces:

- :func:`load_dotenv` — a minimal cwd ``.env`` loader (dev convenience;
  ``setdefault`` semantics so real environment variables always win).
- :func:`register_providers` — registers the built-in provider adapters on the
  global API registry (idempotent).
- :func:`resolve_model` — resolves the :class:`Model` to drive a turn, from the
  flags, the env, the static catalog and (optionally) the live ``ModelRegistry``.
  OpenRouter (OpenAI-compatible) is configured purely from env: when
  ``OPENROUTER_API_KEY`` + a model id are present (and no conflicting
  ``--provider``), a model with ``provider="openrouter"``,
  ``api="openai-completions"`` and the OpenRouter ``base_url`` is built. The
  ``openai_completions`` adapter reads ``OPENROUTER_API_KEY`` from the
  environment itself, so no auth callback wiring is required. Falls back to a
  bare ``Model`` (from ``--model`` / ``--provider``) otherwise — which CANNOT
  drive a turn, so callers gate on ``core.runnable_models.is_runnable`` (#98).
  This function owns the ENTIRE provider-precedence ladder (explicit flag →
  in-id prefix → OpenRouter env → settings default); callers pass each source in
  its own parameter and must never pre-merge them, because the earlier rungs are
  gated on the later ones being absent.

Provider registration + ``.env`` load run from the real console entry
(:func:`aelix_coding_agent.cli.entry.main_sync`), NOT from ``_async_main`` — so
embedders / tests that call ``_async_main`` directly keep deterministic,
side-effect-free behavior.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from aelix_ai.providers import anthropic as _anthropic
from aelix_ai.providers import google_generative_ai as _google_generative_ai
from aelix_ai.providers import google_vertex as _google_vertex
from aelix_ai.providers import openai_codex_responses as _openai_codex_responses
from aelix_ai.providers import openai_completions as _openai
from aelix_ai.providers import openai_responses as _openai_responses
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_API
from aelix_ai.streaming import Model

_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def load_dotenv(path: str = ".env") -> None:
    """Load ``KEY=VALUE`` pairs from a cwd ``.env`` into ``os.environ``.

    ``setdefault`` semantics: a value already present in the real environment
    is never overwritten. Lines that are blank, comments (``#``), or lack ``=``
    are skipped; surrounding single/double quotes on the value are stripped.
    """

    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def register_providers() -> None:
    """Register the built-in provider adapters (idempotent)."""

    _openai.register_all()
    _anthropic.register_all()
    # #15 Workflow B — un-hide the OpenAI Responses adapter (openai 42 +
    # github-copilot 7 + cloudflare-ai-gateway 16 + opencode 16). This surfaces
    # the previously-blocked ``openai-responses`` models in the /model picker;
    # auth resolves from env keys (OPENAI_API_KEY / COPILOT_GITHUB_TOKEN /
    # CLOUDFLARE_API_KEY / OPENCODE_API_KEY) via ``_resolve_client_api_key``.
    # cloudflare-ai-gateway carries a templated base_url whose
    # ``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}`` tokens are
    # expanded from the environment at client construction; until both are set
    # those models stay hidden (``runnable_models`` placeholder guard) instead
    # of failing at the first turn with a malformed URL.
    _openai_responses.register_all()
    # #15 / Phase B §4.1 item #6 — register the OpenAI **Codex** Responses
    # adapter (``openai-codex-responses``). Without it, the 10 ``openai-codex``
    # catalog models resolve auth via ChatGPT Plus/Pro OAuth (so they appear in
    # ``/scoped-models``) but ``partition_runnable`` HIDES them from the
    # ``/model`` picker because their ``api`` had no registered provider. This
    # is the fix for that split-visibility bug.
    _openai_codex_responses.register_all()
    # #15 Workflow B — un-hide the native Gemini adapters. ``google`` (Gemini
    # Developer API, ``google-generative-ai``) surfaces the 29 catalog models +
    # the 2 opencode-zen gemini models (provider=opencode, served via the
    # google-generative-ai protocol at ``opencode.ai/zen/v1/models/{id}``,
    # authenticating from ``OPENCODE_API_KEY``); a missing ``GEMINI_API_KEY``
    # gives a normal "no API key" error, so they surface unconditionally.
    # ``google-vertex`` surfaces its 15 catalog models, but ``runnable_models``
    # keeps them HIDDEN until GCP auth is resolvable (GOOGLE_CLOUD_API_KEY, or a
    # project + GOOGLE_CLOUD_LOCATION) — the cloudflare "never surface a model
    # that errors at turn-1 for missing required config" precedent.
    _google_generative_ai.register_all()
    _google_vertex.register_all()


def _registry_lookup(registry: Any, provider: str, model_id: str) -> Model | None:
    """Resolve ``model_id`` against the LIVE :class:`ModelRegistry`.

    The static catalog is a build-time snapshot. The registry additionally holds
    ``models.json`` custom providers and — once ``bind_model_registry`` has
    replayed them — extension ``register_provider`` models. Neither is knowable
    from the catalog, so without this lookup they resolve to ``api="unknown"``
    and raise the internal "No provider registered for api='unknown'" at the
    first turn (#98).

    An EMPTY ``provider`` is resolved across providers and accepted ONLY when
    exactly one provider serves ``model_id``. An owner guess would dispatch the
    turn — and the credentials with it — to whichever vendor sorted first: the
    bundled catalog alone serves ``gpt-5.4`` from six providers (openai,
    azure-openai-responses, github-copilot, opencode, openai-codex,
    cloudflare-ai-gateway). Ambiguity therefore stays unresolved on purpose and
    the caller's ``is_runnable`` gate points the user at ``/model``.

    A hit is returned VERBATIM, including one whose ``base_url`` is empty (an
    extension ``register_provider`` model can omit it; step 3b of
    ``ModelRegistry._load_models`` merges it without injecting a host). Such a
    model must NOT be dropped to "no match" here: :func:`_sibling_backfill` would
    then stamp the catalog's unanimous api over the api this provider's own
    registration declared, misrouting the turn on a second axis. It is instead
    caught downstream by ``core.runnable_models.is_runnable``, which refuses a
    hostless model precisely because the adapter would resolve it to its SDK's
    first-party vendor host (#98) — the same gate covers the ``/model`` picker,
    which hands registry models straight to ``set_model``.

    Introspection-only: an alternate registry lacking ``find`` / ``get_all``
    degrades to "no match" and must never break launch.
    """

    if registry is None or not model_id:
        return None
    try:
        if provider:
            return registry.find(provider, model_id)
        matches = [m for m in registry.get_all() if m.id == model_id]
        if len({m.provider for m in matches}) == 1:
            return matches[0]
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return None
    return None


def _sibling_backfill(provider: str, model_id: str) -> Model | None:
    """Backfill ``api``/``base_url`` for an uncatalogued id under a KNOWN provider.

    Lets a custom / newly-released id under a catalogued provider still reach an
    adapter. Only an UNANIMOUS sibling ``api`` is adopted: five catalog providers
    span several apis (github-copilot, opencode, cloudflare-ai-gateway,
    fireworks, opencode-go) and every one of them includes ``anthropic-messages``,
    so the previous ``siblings[0].api`` guess routed a github-copilot id to the
    ANTHROPIC adapter (its first sibling is claude-haiku-4.5). That adapter does
    ``base_url=model.base_url or None`` (``providers/anthropic.py``), collapsing
    the omitted base_url to the AsyncAnthropic default host — so a GitHub Copilot
    OAuth bearer left the process for ``api.anthropic.com`` (#98).

    A unanimous ``api`` means every sibling agrees this provider speaks that
    protocol, so the adapter choice cannot cross vendors. ``base_url`` is carried
    only when it too is unanimous, pinning the host explicitly rather than
    relying on an SDK default (amazon-bedrock is the one single-api provider with
    several base_urls — same vendor, different regions).
    """

    from aelix_ai.models import get_models

    siblings = get_models(provider)
    if not siblings:
        return None
    apis = {m.api for m in siblings}
    if len(apis) != 1:
        return None
    base_urls = {m.base_url for m in siblings}
    return Model(
        id=model_id,
        provider=provider,
        api=next(iter(apis)),
        base_url=siblings[0].base_url if len(base_urls) == 1 else "",
    )


def resolve_model(
    model_flag: str | None,
    provider_flag: str | None,
    registry: Any = None,
    default_provider: str | None = None,
) -> Model:
    """Resolve the turn :class:`Model` from flags + env + the live registry.

    Resolution order: (1) OpenRouter-from-env (``OPENROUTER_API_KEY`` + a model
    id, no conflicting ``--provider``); (2) an exact static-catalog hit for
    ``--provider``/``--model``, the ``<provider>/<model>`` slash shorthand, or
    ``default_provider``; (3) ``registry`` — the models.json custom +
    extension-registered providers the build-time catalog cannot know
    (:func:`_registry_lookup`); (4) an uncatalogued id under a catalogued
    provider, backfilled from unanimous siblings (:func:`_sibling_backfill`);
    (5) a bare model whose ``api`` stays the ``Model`` default ``"unknown"``.

    ``provider_flag`` means "the user EXPLICITLY named this provider" (``--provider``)
    and NOTHING else — the OpenRouter-env branch and the slash shorthand are both
    gated on its emptiness, so anything weaker must not be passed through it.
    ``default_provider`` (settings.json ``defaultProvider``) is that weaker
    signal and has its own, lowest-precedence slot below (#98).

    ``registry`` is optional (:data:`None` = catalog-only) because callers resolve
    at points where no registry exists yet. Outcome (5) CANNOT drive a turn, so
    callers MUST gate on ``core.runnable_models.is_runnable`` (#98) — see the
    note at the bare return.
    """

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    model_id = model_flag or os.environ.get("OPENROUTER_DEFAULT_MODEL")
    if openrouter_key and model_id and (provider_flag in (None, "", "openrouter")):
        # Enrich from the Pi catalog when the id is known: a bare Model has
        # ``context_window=0`` / ``max_tokens=0`` / empty cost, which silently
        # disables the context-usage meter (``getContextUsage`` returns None
        # when the window is 0), zeroes ``/cost``, and drops the model's
        # ``thinking_level_map``. The full catalog entry carries all of these.
        # Falls back to a bare model for ids absent from the catalog (custom /
        # newly-released OpenRouter models). Honors a custom OPENROUTER_BASE_URL.
        from dataclasses import replace

        from aelix_ai.models import get_model

        catalog = get_model("openrouter", model_id)
        env_base_url = os.environ.get("OPENROUTER_BASE_URL")
        if catalog is not None:
            return replace(catalog, base_url=env_base_url) if env_base_url else catalog
        return Model(
            id=model_id,
            provider="openrouter",
            api=OPENAI_COMPLETIONS_API,
            base_url=env_base_url or _DEFAULT_OPENROUTER_BASE_URL,
        )
    # Explicit --provider/--model path. Three enrichments over the old bare
    # ``Model(id, provider)`` return, which left ``api="unknown"`` (streaming.py
    # Model default) and so made the stream loop raise the internal
    # ``No provider registered for api='unknown'. Sprint 6a ... register_all()``
    # error for the documented flagship commands (e.g.
    # ``aelix --provider anthropic --model claude-sonnet-4-6 -p hi``):
    #
    #  1. ``<provider>/<model>`` slash shorthand — split it when no separate
    #     ``--provider`` was given (Pi ``resolveModelFromCli`` main.ts:303-304),
    #     so ``aelix --model openai/gpt-4o-mini`` resolves ``provider=openai``
    #     instead of falling through with an empty provider ("No model selected").
    #     Guarded by the OpenRouter branch above: with an ``OPENROUTER_API_KEY``
    #     set, ``openai/gpt-4o-mini`` is (correctly) an OpenRouter model id and
    #     never reaches here.
    #  2. ``default_provider`` — see below; strictly weaker than both 1 and the
    #     OpenRouter branch, so it is applied only after they decline.
    #  3. Catalog enrichment — resolve the full Pi catalog entry (carrying the
    #     real ``api``, context window, cost, thinking map), then the live
    #     registry, then unanimous siblings. Catalogued ids are always exact; the
    #     later steps are the best-effort tail for ids the catalog never saw.
    provider = provider_flag or ""
    resolved_id = model_flag or ""
    if not provider and "/" in resolved_id:
        provider, _, resolved_id = resolved_id.partition("/")
    # (2) settings.json ``defaultProvider`` — the LOWEST-precedence provider
    # source, applied only once every stronger signal has declined. It is a
    # SEPARATE parameter, never folded into ``provider_flag``, because the
    # OpenRouter branch and the slash split are both gated on that flag being
    # empty: a persisted default routed through it silently disables them —
    # ``--model openai/gpt-4o-mini`` ignores its own ``openai/`` prefix and an
    # ``OPENROUTER_API_KEY`` user is locked out of OpenRouter. Both then land on
    # a DIFFERENT vendor holding an id it never heard of, and both still satisfy
    # ``is_runnable`` (the default provider's own api backfills cleanly), so no
    # downstream gate can catch it (#98).
    if not provider:
        provider = default_provider or ""
    if resolved_id:
        from aelix_ai.models import get_model

        if provider:
            catalog = get_model(provider, resolved_id)
            if catalog is not None:
                return catalog
        found = _registry_lookup(registry, provider, resolved_id)
        if found is not None:
            return found
        if provider:
            backfilled = _sibling_backfill(provider, resolved_id)
            if backfilled is not None:
                return backfilled
    # Bare model — ``api`` stays the ``Model`` default "unknown": no catalog
    # entry, no registry entry, and no unanimous sibling api to adopt. Driving a
    # turn with it raises the internal "No provider registered for api='unknown'"
    # from the PROTECTED ``aelix_ai.api_registry``, so every caller must first
    # gate on ``core.runnable_models.is_runnable``: print/json refuses the run,
    # the TUI warns at startup and points at ``/model``.
    #
    # That gate CANNOT be a ``not model.provider`` emptiness check: an
    # uncatalogued provider (a models.json custom, an extension
    # ``register_provider``, or a plain typo) is non-empty and sails straight
    # past it into the raw adapter error (#98).
    return Model(id=resolved_id, provider=provider)


def enrich_copilot_base_url(model: Model, registry: Any) -> Model:
    """Adopt the registry's proxy-ep ``base_url`` for a github-copilot turn model.

    :func:`resolve_model` (→ :func:`aelix_ai.models.get_model`) returns the RAW
    catalog entry whose ``base_url`` is the STATIC default host
    ``https://api.individual.githubcopilot.com``. The token-derived proxy-ep host
    (which DIFFERS for GitHub Copilot Business/Enterprise seats) is injected only
    by ``OAuthProvider.modify_models`` inside :meth:`ModelRegistry._load_models`,
    so it reaches only the interactive ``/model`` picker — every non-picker path
    (CLI ``--print``, TUI startup/default, ``/model <id>``) dispatches to the
    static individual host. On an individual account that host coincidentally
    equals the proxy-ep so the bug is invisible; on an enterprise/business seat
    whose ``proxy-ep=`` names a different host, the request hits the WRONG host →
    httpx "Connection error".

    This adopts the registry copy's ``base_url`` (already modify_models-injected,
    because the registry is built AFTER ``auth_storage.load()``) for
    github-copilot models only, leaving every other provider — including
    OpenRouter's env ``OPENROUTER_BASE_URL`` override baked into ``model`` — intact.
    A ``registry`` miss (uncatalogued id) or a missing registry falls back to the
    input model unchanged.
    """

    if registry is None or getattr(model, "provider", None) != "github-copilot":
        return model
    found = registry.find(model.provider, model.id)
    if found is not None and found.base_url and found.base_url != model.base_url:
        return replace(model, base_url=found.base_url)
    return model


__all__ = [
    "enrich_copilot_base_url",
    "load_dotenv",
    "register_providers",
    "resolve_model",
]
