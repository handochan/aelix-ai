"""Extension API and loader (Phase 1.2).

Public surface:

- :class:`Extension` — the dataclass populated while a factory runs.
- :class:`ExtensionAPI` — the façade passed to extension factories.
- :class:`ExtensionContext` — the handle a hook handler receives at emit time.
- :class:`ExtensionError` — surfaced on stale/unbound runtime usage.
- :func:`load_extensions` / :func:`load_extension_from_factory` — loader entry
  points returning a single shared :class:`_ExtensionRuntime`.
"""

from aelix.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    ExtensionError,
    ExtensionFactory,
    ExtensionFlag,
    ExtensionRuntimeActions,
    _ExtensionRuntime,
)
from aelix.extensions.loader import (
    ExtensionLoadError,
    LoadExtensionsResult,
    load_extension_from_factory,
    load_extensions,
)

__all__ = [
    "Extension",
    "ExtensionAPI",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionLoadError",
    "ExtensionRuntimeActions",
    "LoadExtensionsResult",
    "_ExtensionRuntime",
    "load_extension_from_factory",
    "load_extensions",
]
