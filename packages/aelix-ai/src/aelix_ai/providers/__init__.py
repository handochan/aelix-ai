"""Provider adapters — Sprint 6a (Phase 4.1, ADR-0045).

Pi parity: ``packages/ai/src/providers/`` (SHA 734e08e). Adapters
implement the :class:`Provider` Protocol from :mod:`aelix_ai.providers._base`
and register themselves on the global registry via
``aelix_ai.api_registry.register_provider_object``.

Sprint 6a ships the Anthropic adapter; Sprint 6b lands OpenAI +
OpenRouter (ADR-0045 §F).

Note: the Anthropic submodule is **NOT** eagerly imported here to
avoid a circular dependency with :mod:`aelix_ai.api_registry`. Import
``from aelix_ai.providers.anthropic import register_all`` explicitly.
"""

from aelix_ai.providers._base import Provider, _BareStreamFnProvider

__all__ = [
    "Provider",
    "_BareStreamFnProvider",
]
