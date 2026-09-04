"""The dialect comes from the RESOLVED shell, and the module graph has no cycle.

#204 criterion 1, ADR-0237. ``dialect_for_shell`` is the only place that turns a
shell into a syntax, and it reads the frozensets that already exist
(``_CLASSIFIABLE_SHELLS``, ``_POWERSHELL_NAMES``, ``_CMD_NAMES``) rather than a
fourth spelling of the same names.

These tests RUN on Linux, macOS and the gating ``windows-latest`` leg alike:
nothing here touches the filesystem or ``sys.platform``, it passes shell PATHS
as strings, so both the POSIX and the Windows answers are asserted rather than
skipped (``tests/conftest.py`` doctrine — skip only the genuinely unreachable).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from aelix_coding_agent.builtin.bash_classifier import (
    _CLASSIFIABLE_SHELLS,
    Verdict,
    is_classifiable_shell,
)
from aelix_coding_agent.builtin.shell_classifiers import classify_for_shell
from aelix_coding_agent.builtin.shell_classifiers.dialect import (
    Dialect,
    dialect_for_shell,
)
from aelix_coding_agent.tools.bash import _CMD_NAMES, _POWERSHELL_NAMES

# Verdicts are compared by VALUE here, never by identity: this module is
# imported before ``test_bash_classifier.py::test_module_reimport_is_clean``
# runs, and that reload rebinds ``Verdict`` to a new class object while this
# module still holds the old one. ``IntEnum`` compares by int across both.

_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
_CMD = r"C:\Windows\system32\cmd.exe"


# === dialect_for_shell ======================================================

_SHELL_DIALECTS: dict[str, Dialect] = {
    "/bin/bash": Dialect.POSIX,
    "/bin/sh": Dialect.POSIX,
    "/usr/bin/zsh": Dialect.POSIX,
    "/bin/dash": Dialect.POSIX,
    "ksh93": Dialect.POSIX,  # the version suffix is stripped by shell_basename
    "/usr/local/bin/bash-5.2": Dialect.POSIX,
    _POWERSHELL: Dialect.POWERSHELL,
    _PWSH: Dialect.POWERSHELL,
    "pwsh": Dialect.POWERSHELL,
    _CMD: Dialect.CMD,
    # UNKNOWN, not CMD: ``shell_basename`` strips ``.exe`` and nothing else, so
    # the DOS-era name never matches ``_CMD_NAMES``. Pinned in the safe
    # direction — it keeps today's downgrade-every-ALLOW path.
    "command.com": Dialect.UNKNOWN,
    # UNKNOWN, never POSIX: fish's syntax diverges far enough that the command
    # NAME the bash grammar extracts can be wrong, which is why it is outside
    # ``_CLASSIFIABLE_SHELLS`` in the first place.
    "/usr/bin/fish": Dialect.UNKNOWN,
    "fish-3.6": Dialect.UNKNOWN,
    "nu": Dialect.UNKNOWN,
    "": Dialect.UNKNOWN,
}


@pytest.mark.parametrize(("shell", "expected"), list(_SHELL_DIALECTS.items()))
def test_dialect_for_shell(shell: str, expected: Dialect) -> None:
    assert dialect_for_shell(shell) is expected, shell


def test_no_windows_shell_is_classifiable() -> None:
    """Criterion 1 asserted on the frozenset, not on a behaviour.

    ``is_classifiable_shell`` keeps its exact meaning — "the BASH grammar
    describes this shell" — which is a claim about a grammar, not about a
    platform. #204 adds dialects beside it and must never quietly widen it.
    """

    for name in (*_POWERSHELL_NAMES, *_CMD_NAMES, _POWERSHELL, _PWSH, _CMD):
        assert name not in _CLASSIFIABLE_SHELLS
        assert is_classifiable_shell(name) is False


# === classify_for_shell routing =============================================


def test_posix_shell_routes_to_the_bash_classifier() -> None:
    # Criterion 5: a POSIX absolute path is an input file, not a cmd switch.
    assert classify_for_shell("sort /etc/hosts", "/bin/bash") == Verdict.ALLOW
    assert classify_for_shell("rm -rf /", "/bin/bash") == Verdict.DENY


def test_unknown_shell_gets_the_strictest_dialect() -> None:
    # ``fish`` never becomes POSIX, so the ``/``-leading token stays ambiguous.
    assert classify_for_shell("sort /etc/hosts", "/usr/bin/fish") == Verdict.ASK
    assert classify_for_shell("rm -rf /", "/usr/bin/fish") == Verdict.DENY


@pytest.mark.parametrize("shell", [_POWERSHELL, _PWSH])
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Get-ChildItem", Verdict.ALLOW),
        # ``dir`` is a PowerShell ALIAS of Get-ChildItem, not cmd's ``dir``.
        ("dir", Verdict.ALLOW),
        ("sort /etc/hosts", Verdict.ALLOW),
        ("ls -la", Verdict.ASK),
        # ASK, not DENY: ``-rf`` prefixes neither ``Recurse`` nor ``Force``, so
        # the recursive-force rule does not fire. ``rm -rf /`` still BLOCKS at
        # the gate — through the bash DENY floor in the dispatcher, which is the
        # asymmetry ADR-0237 records rather than hides.
        ("rm -rf /", Verdict.ASK),
        ("Remove-Item -Recurse -Force C:\\", Verdict.DENY),
    ],
)
def test_powershell_routes_to_the_powershell_tables(
    shell: str, command: str, expected: Verdict
) -> None:
    """Slice 2 moved these off the stub tier; this is where that had to happen.

    ``tests/builtin/test_powershell_classifier.py`` owns the full table. What is
    asserted HERE is the routing: a ``pwsh``/``powershell`` path reaches the
    PowerShell grammar rather than the bash one, so ``dir`` and ``Get-ChildItem``
    stop being unknown names.
    """

    assert classify_for_shell(command, shell) == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # ``dir`` is cmd's own listing here, not the PowerShell alias — same
        # word, two dialects, and this is the pair that shows the routing is
        # real rather than a shared table.
        ("dir", Verdict.ALLOW),
        ("Get-ChildItem", Verdict.ASK),  # no such name in cmd
        ("ls -la", Verdict.ASK),
        # ASK, not DENY: ``rm`` is not a cmd name at all. ``rm -rf /`` still
        # BLOCKS at the gate through the bash DENY floor in the dispatcher —
        # the same asymmetry ADR-0237 records for ``pwsh``.
        ("rm -rf /", Verdict.ASK),
        # A ``/``-leading token is a SWITCH to cmd's ``sort``, so criterion 5's
        # recovery is POSIX-only by construction.
        ("sort /etc/hosts", Verdict.ASK),
        (r"del /s /q C:\Windows", Verdict.DENY),
    ],
)
def test_cmd_routes_to_the_cmd_tables(command: str, expected: Verdict) -> None:
    """Slice 3 moved these off the stub tier; this is where that had to happen.

    ``tests/builtin/test_cmd_classifier.py`` owns the full table. What is
    asserted HERE is the routing: a ``cmd.exe`` path reaches the hand-written
    lexer rather than the bash grammar or the PowerShell one.
    """

    assert classify_for_shell(command, _CMD) == expected, command


def test_classify_for_shell_never_raises() -> None:
    """A ``None`` shell is a TypeError inside; the entry point must still ASK."""

    assert classify_for_shell("ls -la", None) == Verdict.ASK  # type: ignore[arg-type]


# === no import cycle ========================================================

_MODULES = [
    "aelix_coding_agent.builtin.bash_classifier",
    "aelix_coding_agent.builtin.shell_classifiers",
    "aelix_coding_agent.builtin.shell_classifiers.dialect",
    "aelix_coding_agent.builtin.shell_classifiers.common",
    "aelix_coding_agent.builtin.shell_classifiers.powershell",
    "aelix_coding_agent.builtin.shell_classifiers.cmd",
]


@pytest.mark.parametrize("module", _MODULES)
def test_each_module_imports_alone(module: str) -> None:
    """Each module must import first, in a fresh interpreter, with nothing else.

    ``bash_classifier`` imports ``Dialect`` at module scope while ``powershell``
    and ``cmd`` import ``Verdict`` back out of ``bash_classifier``, so the
    package ``__init__`` may not import those two eagerly. Measured with the
    eager form in place::

        ImportError: cannot import name 'Verdict' from partially initialized
        module 'aelix_coding_agent.builtin.bash_classifier' (most likely due to
        a circular import)

    A subprocess is the point: an in-process ``import`` would find whatever the
    rest of the suite already loaded and prove nothing about the ORDER.
    """

    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
