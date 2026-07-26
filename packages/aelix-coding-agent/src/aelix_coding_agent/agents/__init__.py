"""Agent profiles — declarative single-agent identity (ADR-0196).

AELIX-ORIGINAL product-core package. An *agent profile* is a single
``agents/<name>.md`` — YAML frontmatter (identity + capability grants) over a
markdown body (the system prompt) — that ``--agent <name>`` / ``--agent-file
<path>`` applies to a WHOLE session, and that ``/agents list|show|use`` renders
and swaps.

Layering, deliberately flat:

* :mod:`.profile`   — the format: parse + validate one file → :class:`AgentProfile`.
* :mod:`.discovery` — where files live, which scope they effectively belong to,
  and the fatal resolve path for ``--agent``.
* :mod:`.resolver`  — the pure projection onto the CLI surface: an argv (dry-run
  rendering) and an in-process :class:`Args` overlay, pinned equivalent.
* :mod:`.prompt`    — the one mirrored kernel join, needed to apply a new
  identity to a LIVE harness without a reload.
* :mod:`.service`   — the stateful ``/agents list|show|use`` surface, held over
  the one mutable ``Args`` the harness factory closes over.

Phase 1 (this package) ships identity only. **Nothing spawns**: there is no
subagent runtime, no ``agent`` tool, no delegation — those are Phase 2, and the
profile fields that only they consume (``role`` / ``output_cap`` /
``timeout_ms`` / ``approval_mode``) are parsed and validated here purely so the
on-disk format never changes shape between phases.

The kernel (``aelix-agent-core``) is not touched by any of this.
"""

from __future__ import annotations

from .discovery import (
    ProfileDiscoveryResult,
    ProfileError,
    classify_scope,
    discover_profiles,
    load_profile_file,
    project_agents_dir,
    resolve_profile,
    user_agents_dir,
)
from .profile import (
    AgentProfile,
    ParseProfileResult,
    ProfileDiagnostic,
    ProfileDiagnosticCode,
    ProfileScope,
    parse_profile,
)
from .prompt import compose_system_prompt
from .resolver import (
    PROFILE_OVERLAY_FIELDS,
    ProfileApplication,
    apply_profile_to_args,
    profile_to_argv,
    profile_to_flags,
)

# ``.service`` imports ``cli.args`` / ``cli.runtime_bootstrap`` / ``tools`` but
# NEVER ``cli.entry`` at module scope (it takes entry's four resolve helpers as
# function-level imports inside ``use``), so re-exporting it here does not close
# a cycle with ``entry.py``'s own module-scope ``from .agents import …``.
# Verified in both import orders.
from .service import AgentProfileService

__all__ = [
    "AgentProfile",
    "AgentProfileService",
    "PROFILE_OVERLAY_FIELDS",
    "ParseProfileResult",
    "ProfileApplication",
    "ProfileDiagnostic",
    "ProfileDiagnosticCode",
    "ProfileDiscoveryResult",
    "ProfileError",
    "ProfileScope",
    "apply_profile_to_args",
    "classify_scope",
    "compose_system_prompt",
    "discover_profiles",
    "load_profile_file",
    "parse_profile",
    "profile_to_argv",
    "profile_to_flags",
    "project_agents_dir",
    "resolve_profile",
    "user_agents_dir",
]
