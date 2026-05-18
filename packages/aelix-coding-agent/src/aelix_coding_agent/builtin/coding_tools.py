"""``coding_tools_extension`` — Aelix-additive extension wrapper.

Pi parity note: Pi does NOT auto-register the 7 built-in tools via an
extension; callers pass them via ``AgentHarnessOptions.tools``. This wrapper
is **Aelix-additive** (ADR-0042 §"Aelix-additive divergence") so users who
prefer the extension-loader path can write ``extensions=[coding_tools_extension(cwd)]``.
"""

from __future__ import annotations

from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)
from aelix_coding_agent.tools import create_all_tools


def coding_tools_extension(
    cwd: str, options: dict | None = None
) -> Extension:
    """Return a single :class:`Extension` registering all 7 coding tools."""

    ext = Extension(name="aelix.coding-tools")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    for tool in create_all_tools(cwd, options).values():
        api.register_tool(tool)
    return ext


__all__ = ["coding_tools_extension"]
