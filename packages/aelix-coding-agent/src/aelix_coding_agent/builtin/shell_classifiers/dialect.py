"""Which shell's SYNTAX a command string is to be read as (#204, ADR-0237).

A LEAF module by construction: it imports nothing else from this package, and
what it does import from ``tools.bash`` and ``bash_classifier`` it imports
INSIDE the function. ``bash_classifier`` imports :class:`Dialect` at module
scope, so anything this file pulled in at module scope would be a cycle —
measured: with ``shell_classifiers/__init__`` importing ``powershell`` eagerly,
``import aelix_coding_agent.builtin.bash_classifier`` raises ``ImportError:
cannot import name 'Verdict' ... (most likely due to a circular import)``.
``tests/builtin/test_shell_dialect.py`` imports each module alone in a
subprocess, which is that claim in executable form.
"""

from __future__ import annotations

from enum import IntEnum


class Dialect(IntEnum):
    """Which shell's SYNTAX a command string is to be read as (#204).

    Ordered so that ``UNKNOWN`` — the default everywhere — is the member that
    assumes nothing. It is NOT a severity order; do not ``max()`` over it the
    way :class:`~aelix_coding_agent.builtin.bash_classifier.Verdict` is
    ``max``ed.
    """

    UNKNOWN = 0
    POSIX = 1
    POWERSHELL = 2
    CMD = 3


def dialect_for_shell(shell: str) -> Dialect:
    """The dialect of the shell ``_resolve_shell`` actually returned.

    ``shell`` is a path (``/bin/bash``, ``C:\\…\\pwsh.exe``) or a bare name.

    Matched against the frozensets that already exist — ``_CLASSIFIABLE_SHELLS``
    for POSIX, ``_POWERSHELL_NAMES`` / ``_CMD_NAMES`` for the two Windows
    families — rather than a fourth spelling of the same names, which is a
    drift bug waiting to happen. Anything unrecognised is ``UNKNOWN`` and never
    ``POSIX``: ``fish`` above all, which is deliberately outside
    ``_CLASSIFIABLE_SHELLS`` because its syntax diverges far enough that the
    extracted command name can be wrong (#204 criterion 1 — the dialect comes
    from the resolved shell, never from ``sys.platform``).
    """

    from aelix_coding_agent.builtin.bash_classifier import (  # noqa: PLC0415
        _CLASSIFIABLE_SHELLS,
    )
    from aelix_coding_agent.tools.bash import (  # noqa: PLC0415
        _CMD_NAMES,
        _POWERSHELL_NAMES,
        shell_basename,
    )

    name = shell_basename(shell)
    if name in _CLASSIFIABLE_SHELLS:
        return Dialect.POSIX
    if name in _POWERSHELL_NAMES:
        return Dialect.POWERSHELL
    if name in _CMD_NAMES:
        return Dialect.CMD
    return Dialect.UNKNOWN


__all__ = ["Dialect", "dialect_for_shell"]
