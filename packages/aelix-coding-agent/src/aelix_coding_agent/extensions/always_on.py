"""The built-in extensions aelix prepends on every run, in one place.

WHY THIS IS NOT IN ``tui/extension_manager.py``, WHERE IT STARTED. It was, and
``cli/status.py`` imported it from there — which measured as 166 extra modules,
including ``prompt_toolkit`` and ``rich``. That is not a startup-cost note: the
``[tui]`` extra is optional, so on a headless install the import does not merely
cost, it **raises**, and ``aelix status`` would have died on a directory listing.
The constant is data about the runtime, not about the viewer that renders it, so
it lives with the runtime and the viewer imports it.

WHAT "ALWAYS ON" MEANS, and what it excludes. These three are appended to
``entry.py``'s ``prepend_extensions`` unconditionally, before any discovered
extension: the guardrail and permission gates, and #101's read-only status
extension. ``AgentsExtension`` is a built-in too and is deliberately NOT here —
it is default-off (``[features] agents``) and additionally gated on
``subagent_depth() < MAX_SUBAGENT_DEPTH``, so "always" would simply be false for
it.
"""

from __future__ import annotations

from typing import Any

#: Class names of the extensions prepended on every run. Class names rather than
#: classes, because the only place this is matched against is a loaded
#: :class:`~aelix_coding_agent.extensions.api.Extension`, whose ``name`` for a
#: prepended factory is ``type(entry).__name__`` — importing the classes here
#: would pull three modules in to compare three strings.
BUILTIN_ALWAYS_ON_NAMES = frozenset(
    {"GuardrailExtension", "PermissionExtension", "StatusExtension"}
)


def is_builtin_always_on(ext: Any) -> bool:
    """True for an Aelix-additive built-in that is prepended on every run.

    Structural marker: a built-in is prepended with NO manifest and a loader name
    equal to its class name. Both halves are required, so a user plugin that
    happened to pick one of these names (and therefore carries a manifest) is
    never mistaken for one.
    """

    if getattr(ext, "manifest", None) is not None:
        return False
    return str(getattr(ext, "name", "") or "") in BUILTIN_ALWAYS_ON_NAMES


__all__ = ["BUILTIN_ALWAYS_ON_NAMES", "is_builtin_always_on"]
