"""Environment-variable API key resolution — Sprint 6b (ADR-0047 §B).

Pi parity: ``packages/ai/src/env-api-keys.ts`` (SHA 734e08e).

The map below mirrors the Pi ``getApiKeyEnvVars`` switch verbatim — same
key set, same priority order. The two lookup helpers reproduce Pi's
``findEnvKeys`` and ``getEnvApiKey`` contracts:

- ``findEnvKeys(provider)`` → list of *configured* env var names (i.e.
  the env vars associated with the provider whose values are non-empty
  in :data:`os.environ`). Returns ``None`` if the provider is unknown
  or no configured env var is set.
- ``get_env_api_key(provider)`` → the value of the first configured env
  var, or ``None`` if no API key is available.

Out of scope for Sprint 6b (deferred to the adapters that own them):

- The ``google-vertex`` ADC + ``GOOGLE_CLOUD_PROJECT`` / ``LOCATION``
  branch — owned by the Google adapter when it ships.
- The ``amazon-bedrock`` AWS credentials branch — owned by the Bedrock
  adapter when it ships.

Per the Pi precedence rule, when a provider has multiple candidate env
vars (``anthropic`` is the only such case today) the first non-empty one
wins.
"""

from __future__ import annotations

import os

# Pi parity: ``getApiKeyEnvVars`` (env-api-keys.ts:91-134). Order
# matters — the first non-empty env var wins in :func:`get_env_api_key`.
# ``anthropic`` keeps OAuth token precedence over the static API key.
ENV_API_KEYS: dict[str, list[str]] = {
    "github-copilot": ["COPILOT_GITHUB_TOKEN"],
    "anthropic": ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "azure-openai-responses": ["AZURE_OPENAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "google": ["GEMINI_API_KEY"],
    "google-vertex": ["GOOGLE_CLOUD_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "vercel-ai-gateway": ["AI_GATEWAY_API_KEY"],
    "zai": ["ZAI_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "minimax-cn": ["MINIMAX_CN_API_KEY"],
    "moonshotai": ["MOONSHOT_API_KEY"],
    "moonshotai-cn": ["MOONSHOT_API_KEY"],
    "huggingface": ["HF_TOKEN"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "opencode": ["OPENCODE_API_KEY"],
    "opencode-go": ["OPENCODE_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY"],
    "cloudflare-workers-ai": ["CLOUDFLARE_API_KEY"],
    "cloudflare-ai-gateway": ["CLOUDFLARE_API_KEY"],
    "xiaomi": ["XIAOMI_API_KEY"],
    "xiaomi-token-plan-cn": ["XIAOMI_TOKEN_PLAN_CN_API_KEY"],
    "xiaomi-token-plan-ams": ["XIAOMI_TOKEN_PLAN_AMS_API_KEY"],
    "xiaomi-token-plan-sgp": ["XIAOMI_TOKEN_PLAN_SGP_API_KEY"],
}


def find_env_keys(provider: str) -> list[str] | None:
    """Return the env var names that currently carry a value for ``provider``.

    Pi parity: ``findEnvKeys`` (``env-api-keys.ts:143``).

    Args:
        provider: provider id (e.g. ``"openai"``, ``"openrouter"``).

    Returns:
        A list of env var names whose values are non-empty in
        :data:`os.environ`, in the Pi-defined precedence order. Returns
        ``None`` if the provider is unknown or none of its candidate env
        vars are set.
    """

    env_vars = ENV_API_KEYS.get(provider)
    if env_vars is None:
        return None
    found = [name for name in env_vars if os.environ.get(name)]
    return found or None


def get_env_api_key(provider: str) -> str | None:
    """Return the API key for ``provider`` from the environment.

    Pi parity: ``getEnvApiKey`` (``env-api-keys.ts:158``). Only the API
    key branch is implemented in Sprint 6b — Vertex AI ADC and Amazon
    Bedrock credential discovery land with their respective adapters.

    Args:
        provider: provider id (e.g. ``"openai"``, ``"openrouter"``).

    Returns:
        The value of the first configured env var for ``provider``, or
        ``None`` if no env var is set.
    """

    env_keys = find_env_keys(provider)
    if env_keys:
        return os.environ.get(env_keys[0])
    return None


__all__ = ["ENV_API_KEYS", "find_env_keys", "get_env_api_key"]
