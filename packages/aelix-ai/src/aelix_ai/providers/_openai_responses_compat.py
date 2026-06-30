"""OpenAI **Responses**-API per-model compat — pi parity.

Pi parity: ``getCompat`` in ``packages/ai/src/api/openai-responses.ts``
(lines 58-64) at SHA ``927e98068cda276bf9188f4774fb927c89823388``::

    function getCompat(model): Required<OpenAIResponsesCompat> {
        return {
            supportsDeveloperRole: model.compat?.supportsDeveloperRole ?? true,
            sendSessionIdHeader: model.compat?.sendSessionIdHeader ?? true,
            supportsLongCacheRetention:
                model.compat?.supportsLongCacheRetention ?? true,
        };
    }

This is a **distinct** compat shape from the OpenAI *completions*
adapter (:class:`aelix_ai.providers._openai_compat.OpenAICompletionsCompat`,
17 fields). The Responses adapter only cares about three per-provider
quirks:

- ``supports_developer_role`` — whether the provider accepts the
  ``developer`` role (vs. falling back to ``system``);
- ``send_session_id_header`` — whether to attach the session-id header
  (some gateways reject unknown headers);
- ``supports_long_cache_retention`` — whether ``cache_retention="long"``
  may be upgraded to the 24h prompt cache.

All three default to ``True`` (the api.openai.com baseline). A model's
``compat`` dict overrides any of them; per pi-SDK convention the dict
may use camelCase keys (``supportsDeveloperRole`` etc.), so we accept
both spellings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_ai.streaming import Model


@dataclass(frozen=True)
class OpenAIResponsesCompat:
    """Resolved Responses-API compat flags (pi ``OpenAIResponsesCompat``).

    Mirrors pi's ``Required<OpenAIResponsesCompat>``: every field is
    populated (no ``None``) and defaults to the api.openai.com baseline.
    """

    supports_developer_role: bool = True
    send_session_id_header: bool = True
    supports_long_cache_retention: bool = True


# snake_case field -> pi camelCase alias accepted in ``model.compat`` dicts.
_CAMEL_ALIASES: dict[str, str] = {
    "supports_developer_role": "supportsDeveloperRole",
    "send_session_id_header": "sendSessionIdHeader",
    "supports_long_cache_retention": "supportsLongCacheRetention",
}


def get_responses_compat(model: Model) -> OpenAIResponsesCompat:
    """Resolve a model's Responses compat, applying ``model.compat`` overrides.

    Pi parity: ``getCompat`` (openai-responses.ts:58-64). The ``compat``
    override may be a dict (camelCase or snake_case keys) or a
    dataclass-style object with snake_case attributes. Missing or
    ``None`` values fall back to the ``True`` baseline.
    """
    override = getattr(model, "compat", None)
    baseline = OpenAIResponsesCompat()
    if override is None:
        return baseline

    def _pick(name: str, default: bool) -> bool:
        if isinstance(override, dict):
            if name in override and override[name] is not None:
                return bool(override[name])
            camel = _CAMEL_ALIASES.get(name)
            if camel is not None and camel in override and override[camel] is not None:
                return bool(override[camel])
            return default
        value = getattr(override, name, None)
        return default if value is None else bool(value)

    return OpenAIResponsesCompat(
        supports_developer_role=_pick(
            "supports_developer_role", baseline.supports_developer_role
        ),
        send_session_id_header=_pick(
            "send_session_id_header", baseline.send_session_id_header
        ),
        supports_long_cache_retention=_pick(
            "supports_long_cache_retention", baseline.supports_long_cache_retention
        ),
    )
