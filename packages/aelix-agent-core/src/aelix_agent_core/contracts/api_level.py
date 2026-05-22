"""Aelix Extension API ABI level (Neovim API_LEVEL pattern).

ADR-0096 §"API_LEVEL policy" — separate from Aelix's own semver to allow
plugin compatibility tracking across breaking changes.
"""

from __future__ import annotations

AELIX_API_LEVEL: int = 1
"""Current Aelix extension API ABI level.

Increment on breaking changes to ANY public extension API
(``ExtensionAPI``, ``ExtensionUIContext``, descriptor schema, manifest
schema). See ADR-0096 §"API_LEVEL policy" for the deprecation cycle.
"""

# Sprint 6h₉a Phase 5b-foundation: API level 1 baseline.
# This is the level emitted by ``AELIX_API_LEVEL`` and the level required by
# ``aelix-plugin.toml`` manifests authored against Phase 5b/5c contracts.


class IncompatibleApiLevelError(ValueError):
    """Raised when the host's API level is below a plugin's required minimum."""


def assert_compatible(plugin_min_level: int, plugin_level: int) -> None:
    """Validate a plugin's declared API levels against the running host.

    Args:
        plugin_min_level: ``aelix-plugin.toml`` ``[plugin.api] min_level`` value.
        plugin_level: ``aelix-plugin.toml`` ``[plugin.api] level`` value.

    Raises:
        IncompatibleApiLevelError: When ``plugin_min_level >
            AELIX_API_LEVEL`` (host too old for the plugin).

    The reverse direction (``plugin_level > AELIX_API_LEVEL`` — plugin built
    for a future API) is currently accepted with a warning (forward-compat
    best-effort).
    """
    if plugin_min_level > AELIX_API_LEVEL:
        raise IncompatibleApiLevelError(
            f"plugin requires AELIX_API_LEVEL >= {plugin_min_level}, "
            f"host is at {AELIX_API_LEVEL}"
        )
    # plugin_level > AELIX_API_LEVEL is forward-compat best-effort (warn only).
    # No-op here; the host loader is responsible for the warning surface.
    _ = plugin_level


__all__ = ["AELIX_API_LEVEL", "IncompatibleApiLevelError", "assert_compatible"]
