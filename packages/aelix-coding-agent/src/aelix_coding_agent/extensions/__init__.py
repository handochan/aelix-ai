"""Extension API and loader (Phase 1.2).

Public surface:

- :class:`Extension` — the dataclass populated while a factory runs.
- :class:`ExtensionAPI` — the façade passed to extension factories.
- :class:`ExtensionContext` — the handle a hook handler receives at emit time.
- :class:`ExtensionError` — surfaced on stale/unbound runtime usage.
- :func:`load_extensions` / :func:`load_extension_from_factory` — loader entry
  points returning a single shared :class:`_ExtensionRuntime`.
"""

from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    ExtensionError,
    ExtensionFactory,
    ExtensionFlag,
    ExtensionRuntimeActions,
    _ExtensionRuntime,
)
from aelix_coding_agent.extensions.command_context import (
    ExtensionCommandContext,
)
from aelix_coding_agent.extensions.ext_ui import ExtensionUIContext
from aelix_coding_agent.extensions.headless_ui import (
    HEADLESS_UI_CONTEXT,
    HeadlessExtensionUIContext,
)
from aelix_coding_agent.extensions.loader import (
    ExtensionLoadError,
    LoadExtensionsResult,
    load_extension_from_factory,
    load_extensions,
)

__all__ = [
    "HEADLESS_UI_CONTEXT",
    "Extension",
    "ExtensionAPI",
    "ExtensionCommandContext",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionLoadError",
    "ExtensionRuntimeActions",
    "ExtensionUIContext",
    "HeadlessExtensionUIContext",
    "LoadExtensionsResult",
    "_ExtensionRuntime",
    "load_extension_from_factory",
    "load_extensions",
]
