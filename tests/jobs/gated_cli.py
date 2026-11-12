"""The aelix CLI with background jobs on: a test launcher (#611 S3c PR c0, #777).

``python tests/jobs/gated_cli.py <aelix arguments>`` is ``aelix <aelix
arguments>`` with ``aelix_coding_agent.jobs.activation.ACTIVE`` set first. A real
CLI subprocess inherits no pytest monkeypatch, and the CLI builds the job
service only while the gate is on (``cli/entry.py``, the ``backgroundJobs``
block), so until #611 S3e flips the product default this is how a test, a pty
capture or the lead's live run reaches a gated CLI. It changes nothing else:
the arguments go to the product's own ``main_sync`` as typed.

Its delegated children run gated too. A child is the product's own command
line, ``[sys.executable, "-P", "-m", "aelix_coding_agent", ...]``
(``aelix_agents.print_channel.build_child_argv``), in a fresh interpreter that
this module's assignment never reaches. So before it calls ``main_sync`` the
launcher puts :data:`GATED_SITE` first on ``PYTHONPATH``, which the child
inherits (``build_child_env`` keeps it). Its ``sitecustomize`` sets the gate in
an interpreter whose main module is ``aelix_coding_agent`` and in nothing else.
The child's argv and environment are otherwise the product's. ``PYTHONPATH`` is
this whole process's, so what the agent's tools run inherits it too: a
``python`` that is not aelix is left alone, and an aelix a tool starts with
``-m aelix_coding_agent`` runs gated.

Test-only: ``tests/`` ships in no wheel or sdist (the root sdist excludes it,
and every wheel names its packages). There is still no flag, environment
variable or setting in the product that turns the gate on (ADR-0293 §12).
"""

from __future__ import annotations

import os
from pathlib import Path

GATED_SITE = Path(__file__).resolve().parent / "gated_site"
"""The directory whose ``sitecustomize`` sets the gate in a delegated child."""


def main() -> None:
    from aelix_coding_agent.jobs import activation

    activation.ACTIVE = True
    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join([str(GATED_SITE), *parts])

    from aelix_coding_agent.cli.entry import main_sync

    main_sync()


if __name__ == "__main__":
    main()
