"""Offline source inspection for the built-in request adapters (#416).

Only a definite absence is False. Unknown/custom adapters are None: their
authentication contract belongs to the extension and must not be invented by
the caller. This module builds no SDK client and resolves no credentials.
"""

from __future__ import annotations

from aelix_ai.api_registry import get_registered_providers
from aelix_ai.streaming import Model, SimpleStreamOptions

from ._env_api_keys import get_env_api_key


def has_configured_request_auth(model: Model, headers: dict[str, str] | None) -> bool | None:
    """Ask the actual built-in adapter's auth acceptance, or defer if unknown.

    The registry has already inspected configured key/record sources. Here we
    inspect the remaining adapter-owned sources and accepted header names.
    A replacement stream for even a known API has an unknown auth contract.
    """

    modules = {
        "anthropic-messages": "anthropic",
        "openai-responses": "openai_responses",
        "openai-completions": "openai_completions",
        "openai-codex-responses": "openai_codex_responses",
        "google-generative-ai": "google_generative_ai",
        "google-vertex": "google_vertex",
    }
    module = modules.get(model.api)
    adapter = get_registered_providers().get(model.api)
    if module is None:
        # Unsupported catalog protocols have no request adapter at all. Only
        # an actual custom dispatch binding has an unknown auth contract.
        return None if adapter is not None else False
    if adapter is not None and type(adapter).__module__ != f"aelix_ai.providers.{module}":
        return None

    if model.api == "anthropic-messages":
        from ._anthropic_client import has_request_auth
        from .anthropic import _resolve_request_auth

        key, token = _resolve_request_auth(model, SimpleStreamOptions(headers=headers or {}))
        return has_request_auth(model.provider, key, token, headers)
    if model.api == "openai-responses":
        from .openai_responses import _resolve_client_api_key

        try:
            _resolve_client_api_key(model.provider, None, headers)
        except RuntimeError:
            return False
        return True
    if model.api == "google-vertex":
        from .google_vertex import GoogleVertexOptions, resolve_vertex_auth

        try:
            resolve_vertex_auth(model, GoogleVertexOptions())
        except RuntimeError:
            return False
        return True
    # Completions, Codex and generative-ai requests require their own key;
    # headers alone do not satisfy their request contract.
    return bool(get_env_api_key(model.provider))
