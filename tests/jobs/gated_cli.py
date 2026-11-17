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

With :data:`ANNOUNCE` set (#777 PR c1's parent-death cases), the launcher and
each gated child write their own pid and start time to that directory
(:func:`announce`). The parent-death cases kill and watch these interpreters
themselves: on the windows runner ``sys.executable`` is the venv launcher, so a
``Popen``'s pid is that launcher's and the aelix it starts has another.

Test-only: ``tests/`` ships in no wheel or sdist (the root sdist excludes it,
and every wheel names its packages). There is still no flag, environment
variable or setting in the product that turns the gate on (ADR-0293 §12).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

GATED_SITE = Path(__file__).resolve().parent / "gated_site"
"""The directory whose ``sitecustomize`` sets the gate in a delegated child."""

ANNOUNCE = "AELIX_TEST_ANNOUNCE"
"""A directory, when set: each gated aelix writes ``<role>-<pid>.json`` there (:func:`announce`)."""


def announce(role: str) -> None:
    """``{"role", "pid", "started"}`` of this interpreter in :data:`ANNOUNCE`, whole at its one ``os.replace``.

    ``started`` is :func:`~aelix_ai.utils._process_identity.process_started`'s,
    the value a test compares to know the pid is still this process. The same
    few lines are in ``gated_site/sitecustomize.py`` (role ``child``), which
    cannot import this module.
    """

    where = os.environ.get(ANNOUNCE)
    if not where:
        return
    from aelix_ai.utils._process_identity import process_started

    pid = os.getpid()
    path = os.path.join(where, f"{role}-{pid}.json")
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"role": role, "pid": pid, "started": process_started(pid)}, handle)
    os.replace(path + ".tmp", path)


def gate() -> None:
    """Background jobs on in this process, and :data:`GATED_SITE` first on the ``PYTHONPATH`` it hands on."""

    from aelix_coding_agent.jobs import activation

    activation.ACTIVE = True
    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join([str(GATED_SITE), *parts])


def main() -> None:
    gate()
    announce("parent")

    from aelix_coding_agent.cli.entry import main_sync

    main_sync()


if __name__ == "__main__":
    main()
