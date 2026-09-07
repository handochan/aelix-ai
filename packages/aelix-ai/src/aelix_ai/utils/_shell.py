"""Which shell this machine has, and how that shell takes a command string.

Private like :mod:`aelix_ai.utils._process_tree`, and here for the same reason:
two callers in two different packages need ONE answer to a Windows question, and
the dependency direction is one-way (``aelix-ai`` <- ``aelix-agent-core`` <-
``aelix-coding-agent``), so the shared piece lives at the bottom. The callers are
``aelix_coding_agent.tools.bash``, which NAMES a shell to run a tool call under,
and ``aelix_ai.oauth._resolve_config``, which SPAWNS one to resolve a
``models.json`` / ``auth.json`` ``!command`` (#227). Before #227 the second
hard-coded ``sh``, which does not exist on a stock Windows box, so the spawn
failed and the failure blamed the user's command.

THE WIN32 CHAIN, best first: ``$SHELL`` when it names a file that exists ->
``sh`` on ``PATH`` (the caller that spawns only) -> ``pwsh`` -> ``powershell``
-> ``%COMSPEC%`` -> ``cmd.exe``. Everything but the ``sh`` step is the chain
``_resolve_shell_win32`` has had since #104, unchanged and in the same order, so
one machine gets one shell answer for both callers. The ``sh`` step is opt-in
(``include_posix_sh``) because a ``!command`` was written for ``sh``, and BEFORE
#227 every Windows box where one worked at all had one, while the bash tool must
not take it: ``sh`` is classifiable, and taking it there would flip AUTO mode's
dialect on every MSYS box, which is ADR-0237/#204's decision rather than #227's.

WHAT THIS MODULE DOES NOT OWN. The family answer is here; each caller's policy
stays with the caller. ``_resolve_config`` adds ``-NoProfile`` and
``-NonInteractive`` to the PowerShell family and ``/d /s`` to the ``cmd`` one,
because a credential command must not run the user's profile — measured on
PowerShell 7, a profile that writes to stdout is prepended to the resolved key
(``profile-banner\\nsk-KEY``) and an unguarded ``Read-Host`` prompt lands inside
it (``'give me a key: \\nGOT:'`` against ``'GOT:'``). The bash tool runs the
user's interactive shell and must keep their profile, so it adds none of that.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# How each shell family takes a command STRING. POSIX shells use ``-c``;
# ``cmd.exe`` accepts only ``/c``; PowerShell is spelled ``-Command`` in full
# (``powershell.exe`` has other ``-C…`` parameters, so the abbreviation is not
# reliably unambiguous across 5.1 and 7).
POSIX_COMMAND_FLAG = "-c"
CMD_COMMAND_FLAG = "/c"
POWERSHELL_COMMAND_FLAG = "-Command"

POWERSHELL_NAMES = frozenset({"pwsh", "powershell"})
CMD_NAMES = frozenset({"cmd", "command"})


# A trailing version on a shell's filename: ``bash-5.2``, ``zsh-5.9``,
# ``ksh93``, ``bash-5.2p26``. Anchored on a DIGIT, so it can only ever shorten
# a name to a shorter one — it cannot invent a match. Removing it is what keeps
# a version-suffixed genuine bash classifiable; see :func:`shell_basename`.
_VERSION_SUFFIX_RE = re.compile(r"[-_]?\d[\d.]*[a-z]*\d*$")


def shell_basename(shell: str) -> str:
    """Canonical shell name from a shell path.

    Lower-cased basename with a ``.exe`` extension and any trailing version
    suffix removed, so ``/usr/local/bin/bash-5.2`` and ``ksh93`` answer to
    ``bash`` and ``ksh``.

    Splits on BOTH separators rather than deferring to :mod:`os.path`, because
    the caller may be reasoning about a Windows path while running on POSIX
    (the permission gate, and the tests that drive it) — ``posixpath.basename``
    would hand back the whole ``C:\\…\\powershell.exe`` string.

    Stripping the version matters for more than tidiness: a distro or Homebrew
    ``bash-5.2`` on ``$SHELL`` would otherwise fail to match ``bash`` and the
    AUTO-mode gate would prompt for every command a real bash was about to run.
    The strip cannot go the other way and wrongly ADMIT a shell — it only ever
    maps a name to a shorter one, and ``fish-3.6`` still resolves to ``fish``,
    which stays out of the classifiable set.
    """

    name = shell.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return _VERSION_SUFFIX_RE.sub("", name) or name


def command_flag_for(shell: str) -> str:
    """The ``run this command string`` flag for ``shell``."""

    name = shell_basename(shell)
    if name in POWERSHELL_NAMES:
        return POWERSHELL_COMMAND_FLAG
    if name in CMD_NAMES:
        return CMD_COMMAND_FLAG
    return POSIX_COMMAND_FLAG


@dataclass(frozen=True)
class ShellConfig:
    """Pi parity ``getShellConfig()`` result: the shell AND how to invoke it.

    The two are inseparable once Windows is in scope. The spawn site used to
    hard-code ``-c``, which is correct for every POSIX shell and wrong for
    ``cmd.exe`` (``/c``) and unreliable for PowerShell (``-Command``), so the
    flag has to be resolved together with the path rather than assumed.
    """

    path: str
    command_flag: str = POSIX_COMMAND_FLAG


def windows_command_shells(
    env: Mapping[str, str], *, include_posix_sh: bool = False
) -> list[ShellConfig]:
    """Every Windows shell candidate, best first.

    ``[0]`` is what ``_resolve_shell_win32`` has always returned; the list
    exists because a caller that SPAWNS can fall through a candidate a caller
    that only NAMES one cannot. ``include_posix_sh`` inserts the ``sh`` step —
    see this module's header for why exactly one caller takes it.

    ``$SHELL`` is honoured ONLY when it names a file that exists, because the
    common way it is set on Windows is Git-Bash exporting the MSYS path
    ``/usr/bin/bash``, which :class:`subprocess.Popen` cannot spawn.

    The PATH probes read ``env["PATH"]`` and are skipped entirely when the key
    is absent. ``shutil.which(…, path=None)`` reads the HOST's ``PATH``
    instead — measured, it finds this darwin box's ``/bin/sh``, so a fixture
    meant as "stock Windows" would silently be ``sh``-present. The
    ``_resolve_config`` caller passes :data:`os.environ`, which always has one;
    the bash tool passes its spawn context's env, which a ``spawn_hook`` may
    rewrite, and a PATH-less env there now yields ``%COMSPEC%``/``cmd.exe``
    instead of falling back to the host ``PATH`` and finding ``pwsh``.
    """

    out: list[ShellConfig] = []
    if (shell := env.get("SHELL")) and Path(shell).exists():
        out.append(ShellConfig(shell, command_flag_for(shell)))
    path = env.get("PATH")
    if path is not None:
        if include_posix_sh and (found := shutil.which("sh", path=path)):
            out.append(ShellConfig(found, POSIX_COMMAND_FLAG))
        for exe in ("pwsh", "powershell"):
            if found := shutil.which(exe, path=path):
                out.append(ShellConfig(found, POWERSHELL_COMMAND_FLAG))
    if comspec := env.get("COMSPEC"):
        out.append(ShellConfig(comspec, command_flag_for(comspec)))
    out.append(ShellConfig("cmd.exe", CMD_COMMAND_FLAG))
    return out
