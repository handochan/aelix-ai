"""Per-dialect AUTO-mode classifiers, selected by the RESOLVED shell (#204).

ADR-0237: a dialect owns its switch syntax. ``bash_classifier`` reads the bash
grammar and, since #204, reads a known read-only name's ARGUMENTS with a
dialect's switch table; this package is where the two non-POSIX dialects get a
grammar of their own.

Import direction, and the cycle it avoids: :mod:`.dialect` is a leaf and is the
ONLY module this ``__init__`` may import at RUNTIME module scope, because
``bash_classifier`` imports :class:`~.dialect.Dialect` at ITS module scope
while :mod:`.powershell` and :mod:`.cmd` import ``Verdict`` back out of
``bash_classifier``. Importing those two here eagerly makes
``import aelix_coding_agent.builtin.bash_classifier`` raise ``ImportError:
cannot import name 'Verdict'`` — measured, not feared. So they are imported
inside :func:`classify_for_shell`, and ``Verdict`` is imported for TYPING only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aelix_coding_agent.builtin.shell_classifiers.dialect import (
    Dialect,
    dialect_for_shell,
)

if TYPE_CHECKING:  # a runtime import here would be the cycle described above
    from aelix_coding_agent.builtin.bash_classifier import Verdict


def classify_for_shell(command: str, shell: str) -> Verdict:
    """Classify ``command`` as the syntax of the shell that will RUN it.

    ``shell`` is the resolved shell path from ``_resolve_shell``; #204
    criterion 1 — the dialect comes from the shell actually resolved, never
    from ``sys.platform`` and never from a default.

    Fails safe to ASK on anything — a missing grammar, an unparsable input, an
    unresolvable shell — mirroring ``bash_classifier._load_parser``. The one
    thing it cannot absorb is ``bash_classifier`` itself failing to import,
    because then there is no ``Verdict`` to return; the permission gate's own
    ``except`` (``permission.py``'s ``_auto_classify_bash``) is the backstop for
    that, and it returns ``"ask"``.
    """

    from aelix_coding_agent.builtin.bash_classifier import (  # noqa: PLC0415
        Verdict,
        classify,
    )

    try:
        dialect = dialect_for_shell(shell)
        if dialect is Dialect.POWERSHELL:
            from aelix_coding_agent.builtin.shell_classifiers.powershell import (  # noqa: PLC0415
                classify_powershell,
            )

            return classify_powershell(command)
        if dialect is Dialect.CMD:
            from aelix_coding_agent.builtin.shell_classifiers.cmd import (  # noqa: PLC0415
                classify_cmd,
            )

            return classify_cmd(command)
        return classify(command, dialect=dialect)
    except Exception:  # noqa: BLE001 — any classifier failure → ASK (safe)
        return Verdict.ASK


__all__ = ["Dialect", "classify_for_shell", "dialect_for_shell"]
