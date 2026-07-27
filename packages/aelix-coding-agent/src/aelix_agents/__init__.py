"""``aelix-agents`` — the bundled single-mode delegation extension (ADR-0197).

A SEPARATE TOP-LEVEL IMPORT PACKAGE, shipped inside the existing
``aelix-coding-agent`` wheel. Both halves of that sentence are deliberate.

Separate package, because the 3-band rule (ADR-0008, amended by ADR-0197) gives
product-core the CONTRACT (``aelix_coding_agent.subagent_contract``) and this
package every process-touching, argv-building, stream-parsing and consent-
authoring statement. ``tests/agents/test_p2_band_boundaries.py`` is the machine
gate on that split, and the import direction is one-way: ``aelix_agents.*`` may
import ``aelix_coding_agent.*``, while product-core names ``aelix_agents`` at
exactly ONE site — a function-level import inside ``cli/entry.py::_async_main``.

Same wheel, because a separate distribution would trip
``tests/test_license_sync.py``, ``tests/test_release_version_consistency.py``,
``scripts/generate_sbom.py``, ``RELEASING.md``, a new PyPI pending publisher and
``install.sh`` — six real costs for a boundary the tests already enforce. The
packaging half is one line in ``pyproject.toml``'s wheel ``packages`` list.

The public surface is deliberately one name. Everything else in this package is
an implementation detail of it, and product-core must not learn any of it — most
importantly not :class:`~aelix_agents.consent.SpawnGrant`, which is what keeps
consent policy out of band 2 (``test_protocol_has_no_consent_parameter``).
"""

from __future__ import annotations

from aelix_agents.extension import AgentsExtension

__all__ = [
    "AgentsExtension",
]
