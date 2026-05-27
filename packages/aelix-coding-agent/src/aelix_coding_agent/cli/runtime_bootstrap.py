"""CLI runtime bootstrap — provider registration + .env load + model resolution.

Wires real LLM turns for the interactive / print / rpc CLI. Three pieces:

- :func:`load_dotenv` — a minimal cwd ``.env`` loader (dev convenience;
  ``setdefault`` semantics so real environment variables always win).
- :func:`register_providers` — registers the built-in provider adapters on the
  global API registry (idempotent).
- :func:`resolve_model` — resolves the :class:`Model` to drive a turn. OpenRouter
  (OpenAI-compatible) is configured purely from env: when ``OPENROUTER_API_KEY``
  + a model id are present (and no conflicting ``--provider``), a model with
  ``provider="openrouter"``, ``api="openai-completions"`` and the OpenRouter
  ``base_url`` is built. The ``openai_completions`` adapter reads
  ``OPENROUTER_API_KEY`` from the environment itself, so no auth callback wiring
  is required. Falls back to the prior bare ``Model`` (from ``--model`` /
  ``--provider``) otherwise.

Provider registration + ``.env`` load run from the real console entry
(:func:`aelix_coding_agent.cli.entry.main_sync`), NOT from ``_async_main`` — so
embedders / tests that call ``_async_main`` directly keep deterministic,
side-effect-free behavior.
"""

from __future__ import annotations

import os
from pathlib import Path

from aelix_ai.providers import anthropic as _anthropic
from aelix_ai.providers import openai_completions as _openai
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


def resolve_model(model_flag: str | None, provider_flag: str | None) -> Model:
    """Resolve the turn :class:`Model` from flags + env (OpenRouter-aware)."""

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
    # Existing behavior: a bare model from explicit flags (the adapter resolves
    # the per-provider key from env when the api is registered).
    return Model(id=model_flag or "", provider=provider_flag or "")


__all__ = ["load_dotenv", "register_providers", "resolve_model"]
