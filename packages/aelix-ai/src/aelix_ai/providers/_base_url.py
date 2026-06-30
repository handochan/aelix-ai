"""Base-URL placeholder expansion — pi parity (``cloudflare-auth.ts``).

pi expands ``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}`` tokens in a
model ``baseUrl`` from the resolved auth env (``resolveCloudflareBaseUrl``,
``packages/ai/src/providers/cloudflare-auth.ts``) before constructing the
client. The placeholder name *is* the env-var name, so the faithful, general
form is: substitute any ``{ENV_VAR}`` token with ``os.environ[ENV_VAR]`` when
that variable is set, leaving the token verbatim otherwise.

A base_url that still carries a ``{…}`` token after expansion means a required
env var is missing — the SDK would receive a malformed URL and fail at the
first turn — so callers treat such a model as not runnable / keep it hidden
(see :mod:`aelix_coding_agent.core.runnable_models`).
"""

from __future__ import annotations

import os
import re

# Placeholder token: ``{ENV_VAR_NAME}`` (matches pi's ``{CLOUDFLARE_ACCOUNT_ID}``
# / ``{CLOUDFLARE_GATEWAY_ID}``). Env-var names are letters/digits/underscore.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_base_url(base_url: str | None) -> str | None:
    """Substitute ``{ENV_VAR}`` tokens in ``base_url`` from the environment.

    Each ``{NAME}`` is replaced by ``os.environ[NAME]`` when that variable is
    set and non-empty; an unset variable's token is left verbatim so
    :func:`has_unexpanded_placeholders` can flag the model as not runnable.
    Returns the input unchanged when it is falsy or carries no placeholder.
    """

    if not base_url or "{" not in base_url:
        return base_url

    def _sub(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        return value if value else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, base_url)


def unexpanded_placeholder_names(base_url: str | None) -> list[str]:
    """Env-var names whose ``{NAME}`` token is still unfilled after expansion.

    Empty when ``base_url`` is falsy, carries no placeholder, or every token's
    env var is set — i.e. the URL is ready for client construction.
    """

    if not base_url or "{" not in base_url:
        return []
    return [
        name
        for name in (m.group(1) for m in _PLACEHOLDER_RE.finditer(base_url))
        if not os.environ.get(name)
    ]


def has_unexpanded_placeholders(base_url: str | None) -> bool:
    """True when ``base_url`` still carries a ``{ENV_VAR}`` after expansion.

    Signals a required env var is unset → the model would hit a malformed URL
    at the first turn, so callers treat it as not runnable / keep it hidden.
    """

    return bool(unexpanded_placeholder_names(base_url))


__all__ = [
    "expand_base_url",
    "has_unexpanded_placeholders",
    "unexpanded_placeholder_names",
]
