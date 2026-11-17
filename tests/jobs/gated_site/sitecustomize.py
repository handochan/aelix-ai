"""Background jobs on in a delegated child of ``tests/jobs/gated_cli.py`` (#777).

Python imports this at start-up from every interpreter whose ``PYTHONPATH``
lists this directory, which only the launcher puts there. The launcher sets it
for its whole process, so it reaches what the agent's tools run as well as the
delegated child. It acts only in an interpreter whose main module is
``aelix_coding_agent``, the command line a delegated child is given
(``python -P -m aelix_coding_agent …``):

* a ``python`` a tool runs that is not aelix is left alone: ``-c``, a script,
  ``-m`` of another module, and ``python -c '…' -m aelix_coding_agent``, where
  ``-m`` is an argument of the ``-c`` program;
* an aelix that a tool starts with ``-m aelix_coding_agent`` runs gated, as the
  delegated child does.

It hides any other ``sitecustomize`` the interpreter would have imported, so it
imports the first one on ``sys.path`` that is not a copy of itself — a second
checkout's ``gated_site``, or this one by another spelling of its path, is a
copy, known by :data:`_MARK` in its source (``tests/jobs/test_gated_site_777.py``).

With ``AELIX_TEST_ANNOUNCE`` set, an interpreter it gates also writes its pid
and start time there as ``child-<pid>.json`` (:func:`_announce`, the launcher's
``announce`` again: this hook cannot import the launcher). #777 PR c1's
parent-death cases watch a delegated child by that announcement.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys

_MARK = "aelix-gated-site-hook"
"""In this file's source, so in every copy of it; a ``sitecustomize`` without it is foreign."""


def main_module(orig_argv: list[str], argv: list[str]) -> str | None:
    """The module the interpreter runs as ``__main__`` with ``-m``, else ``None``.

    At start-up ``sys.argv[0]`` is ``"-m"`` only when a module is the main
    program (``"-c"`` for ``-c``, the path for a script; measured on 3.11, 3.12
    and 3.14), and the module's name is the ``sys.orig_argv`` token just before
    the program's own arguments: ``aelix_coding_agent``, or an option cluster
    such as ``-Pmaelix_coding_agent`` that ends with it.
    """

    if not argv or argv[0] != "-m" or len(orig_argv) < len(argv):
        return None
    token = orig_argv[len(orig_argv) - len(argv)]
    if not token.startswith("-"):
        return token
    return token[token.index("m", 1) + 1 :] if "m" in token[1:] else None


def _is_a_copy(origin: str | None) -> bool:
    """Whether the ``sitecustomize`` at ``origin`` is this hook; one that cannot be read as UTF-8
    text (compiled, say) is not."""

    if origin is None:
        return False
    try:
        with open(origin, encoding="utf-8") as handle:
            return _MARK in handle.read()
    except (OSError, ValueError):
        return False


def _announce(role: str) -> None:
    """``tests/jobs/gated_cli.py``'s ``announce``, for an interpreter this hook gates."""

    where = os.environ.get("AELIX_TEST_ANNOUNCE")
    if not where:
        return
    from aelix_ai.utils._process_identity import process_started

    pid = os.getpid()
    path = os.path.join(where, f"{role}-{pid}.json")
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"role": role, "pid": pid, "started": process_started(pid)}, handle)
    os.replace(path + ".tmp", path)


def _import_the_next_sitecustomize() -> None:
    for entry in sys.path:
        spec = importlib.machinery.PathFinder.find_spec("sitecustomize", [entry])
        if spec is None or spec.loader is None or _is_a_copy(spec.origin):
            continue
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        return


if main_module(list(getattr(sys, "orig_argv", [])), list(sys.argv)) == "aelix_coding_agent":
    from aelix_coding_agent.jobs import activation

    activation.ACTIVE = True
    _announce("child")

_import_the_next_sitecustomize()
