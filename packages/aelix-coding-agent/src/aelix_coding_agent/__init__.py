"""Aelix coding agent — ExtensionAPI + built-in extensions.

Mirrors pi-coding-agent (non-UI scope): the Extension surface, extension loader,
and the built-in :class:`PolicyExtension` / :class:`GuardrailExtension` plus
example tools live here.
"""

from aelix_coding_agent.builtin import (
    DEFAULT_GUARDRAIL_RULES,
    GuardrailExtension,
    GuardrailRule,
    PolicyExtension,
)
from aelix_coding_agent.extensions import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    ExtensionError,
    ExtensionFactory,
    ExtensionFlag,
    ExtensionLoadError,
    ExtensionRuntimeActions,
    LoadExtensionsResult,
    _ExtensionRuntime,
    load_extension_from_factory,
    load_extensions,
)

__all__ = [
    "DEFAULT_GUARDRAIL_RULES",
    "Extension",
    "ExtensionAPI",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionLoadError",
    "ExtensionRuntimeActions",
    "GuardrailExtension",
    "GuardrailRule",
    "LoadExtensionsResult",
    "PolicyExtension",
    "_ExtensionRuntime",
    "load_extension_from_factory",
    "load_extensions",
]
