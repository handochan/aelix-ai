"""``aelix-status`` — the bundled runtime-introspection extension (#101).

A SEPARATE TOP-LEVEL IMPORT PACKAGE shipped inside the existing
``aelix-coding-agent`` wheel, the same arrangement ADR-0197 chose for
``aelix_agents`` and for the same packaging reason: a separate DISTRIBUTION
would have to be threaded through ``tests/test_license_sync.py``,
``tests/test_release_version_consistency.py``, ``scripts/generate_sbom.py``,
``RELEASING.md``, ``install.sh`` and a new PyPI pending publisher, while a
package DIRECTORY costs one entry in ``[tool.hatch.build.targets.wheel]
packages``. See :mod:`aelix_status.extension` for why it is not inside
``aelix_agents`` and not a ``tools/`` built-in.

WITHOUT THAT PACKAGING ENTRY THIS PACKAGE IS ABSENT FROM THE WHEEL and
``from aelix_status import StatusExtension`` fails for every installed user while
passing in every source checkout. ``tests/status/test_status_wheel.py`` asserts
against a built wheel because nothing read from the source tree can see that.
"""

from __future__ import annotations

from aelix_status.extension import (
    STATUS_TOOL_NAME,
    STATUS_TOOL_PARAMETERS,
    StatusExtension,
    create_status_tool,
)
from aelix_status.snapshot import ExtensionSnapshot, RuntimeSnapshot

__all__ = [
    "STATUS_TOOL_NAME",
    "STATUS_TOOL_PARAMETERS",
    "ExtensionSnapshot",
    "RuntimeSnapshot",
    "StatusExtension",
    "create_status_tool",
]
