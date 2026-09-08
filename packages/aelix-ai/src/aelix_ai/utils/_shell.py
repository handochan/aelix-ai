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
-> ``%COMSPEC%`` -> ``%SystemRoot%\\System32\\cmd.exe`` -> ``cmd.exe``. That is
``_resolve_shell_win32``'s chain since #104 with one candidate added at the
floor (#241) and the order otherwise unchanged, so one machine still gets one
shell answer for both callers. The ``sh`` step is opt-in
(``include_posix_sh``) because a ``!command`` was written for ``sh``, and BEFORE
#227 every Windows box where one worked at all had one, while the bash tool must
not take it: ``sh`` is classifiable, and taking it there would flip AUTO mode's
dialect on every MSYS box, which is ADR-0237/#204's decision rather than #227's.

A SHELL NOBODY NAMED IS TAKEN ONLY WHEN IT IS ABSOLUTE (#241). ``$SHELL`` is
the user's explicit choice and is honoured verbatim; every other candidate is
this module's own guess, and a guess that resolves against the process's
current directory is a program an attacker can drop into a repository. That
rules out :func:`shutil.which` (it searches ``.`` first on win32 even with an
explicit ``path=``), empty and relative ``PATH`` components, a relative
``%COMSPEC%``, and the bare ``cmd.exe`` wherever an absolute one can be
synthesised. It is already this repo's rule for ``uv``
(``cli/extension_install.py``, CWE-426).

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

import ntpath
import os
import posixpath
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# ``shutil._WIN_DEFAULT_PATHEXT``, byte-identical in 3.11.15, 3.12.13, 3.13.13
# and 3.14.5 (read, not assumed). Copied rather than imported because it is
# private: a release that renames it must not take this module's probe with it.
_WIN_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"

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


def _is_absolute(value: str) -> bool:
    """True when ``value`` names a place that cannot be the current directory.

    Both flavours, unconditionally — deliberately not :func:`os.path.isabs`, and
    deliberately not under this module's ``platform`` seam. ``os.path.isabs`` IS
    ``posixpath.isabs`` off Windows, so it reads
    ``C:\\Windows\\system32\\cmd.exe`` as *relative* and makes the whole win32
    chain unassertable from a POSIX box: measured, that spelling turned five
    existing cases red on darwin (``5 failed, 88 passed``) against ``93
    passed``, and would have turned none red on windows-latest.

    The nt half additionally requires a DRIVE, which is 3.13+'s ``ntpath.isabs``
    adopted early. Without that clause a ROOTLESS ``\\dir`` is accepted on
    3.11/3.12 and rejected on 3.13+ (``ntpath``'s own "LEGACY BUG" comment), and
    an accepted one is exactly the answer this module exists to refuse: it is
    drive-relative on Windows, and on POSIX ``os.path.join("\\dir", "sh")`` is a
    plain relative name resolved against the process's current directory
    (measured here on 3.12.13: a planted ``./\\evil/sh`` was returned). Requiring
    the drive costs nothing else — measured on 3.11.15 and 3.12.13, every value
    below answers identically on both, and identically to bare
    ``ntpath.isabs`` on 3.13.

    Rejects ``""``, ``.``, ``cmd.exe``, ``relbin``, ``..\\tools``, the
    drive-RELATIVE ``C:foo`` and the rootless ``\\dir`` on every host and
    interpreter; accepts ``C:\\x``, ``\\\\srv\\share`` and ``/tmp/x`` on all of
    them. Both halves are load-bearing on every leg, which is why neither can be
    deleted with the suite green: ``C:foo`` has a drive and is still relative,
    so ``ntpath.isabs`` is needed; ``/tmp/x`` has no drive, so
    ``posixpath.isabs`` is.
    """

    drive = ntpath.splitdrive(value)[0]
    return (drive != "" and ntpath.isabs(value)) or posixpath.isabs(value)


def _env_get(env: Mapping[str, str], name: str, *, fold: bool) -> str | None:
    """``env[name]``, with one case-insensitive pass only when ``fold``.

    ``fold`` is the Windows NAMING rule, not a convenience. On POSIX ``Path``
    and ``PATH`` are two different variables and the bash tool hands this very
    mapping to :class:`subprocess.Popen`, so folding there would let the
    resolver read one variable while the child is given another.

    What it buys on Windows, honestly: production's two mapping types already
    answer the UPPER spelling, because CPython upper-cases every key of
    :data:`os.environ` on nt (``os.py``'s ``_createenviron``). The fold is for
    hand-built dicts and for an env a ``spawn_hook`` rewrote, where a hook that
    sets ``Path`` on Windows really would reach the child as ``PATH``. Same
    shape as ``util/shell_env.py``'s PATH-key lookup and ``_process_tree.py``'s
    ``SYSTEMROOT`` read.
    """

    value = env.get(name)
    if value is not None or not fold:
        return value
    wanted = name.lower()
    for key, candidate in env.items():
        if key.lower() == wanted:
            return candidate
    return None


def _pathext_names(name: str, *, env: Mapping[str, str], windows: bool) -> list[str]:
    """The filenames Windows would try for ``name``; just ``name`` elsewhere.

    CPython 3.12+'s rule adopted verbatim (``shutil.py:1536-1551`` on 3.12.13):
    one candidate per ``PATHEXT`` entry with a trailing ``.`` stripped, and the
    BARE name inserted first only when the name already ends in a listed
    extension. 3.11 answers differently — it has no ``rstrip`` at all, and a
    name that already carries a listed extension gets ``files = [cmd]`` ALONE
    (``shutil.py:1540-1543`` on 3.11.15) instead of the bare name in FRONT of
    the expansions. Neither divergence can fire for the names this module asks
    for, since none of ``sh``/``pwsh``/``powershell``/``bash`` carries an
    extension; the newer rule is the one adopted because it is the one the
    interpreters past this repo's floor will keep. ``PATHEXT``
    comes from ``env`` and not :data:`os.environ` for the reason ``PATH``
    already does — a ``spawn_hook`` may have rewritten the bash tool's env — and
    an absent key falls back to shutil's own default rather than disabling the
    probe.

    Split on a literal ``";"`` rather than :data:`os.pathsep`, because this runs
    under ``platform="win32"`` on POSIX hosts, where ``os.pathsep`` is ``":"``
    and would shred ``.COM;.EXE;…`` into one entry.

    The bare-name branch and the ``rstrip(".")`` are unreachable for the names
    this module resolves (``sh``, ``pwsh``, ``powershell``, ``bash``): none
    carries an extension. They are kept as a deliberate mirror of CPython so the
    two rules cannot drift, and are pinned by driving :func:`_which_on_path`
    directly — with ``pwsh.exe`` for the branch, and with a ``PATHEXT`` of
    ``".EXE."`` for the strip. The strip's row can only run off Windows: Win32
    path resolution drops a trailing dot, so ``pwsh.EXE.`` opens ``pwsh.EXE``
    there whether or not the strip ran.
    """

    if not windows:
        return [name]
    source = _env_get(env, "PATHEXT", fold=True) or _WIN_DEFAULT_PATHEXT
    exts = [ext for ext in source.split(";") if ext]
    names = [name + ext.rstrip(".") for ext in exts]
    upper = name.upper()
    if any(upper.endswith(ext.upper()) for ext in exts):
        names.insert(0, name)
    return names


def _which_on_path(
    name: str, *, path: str | None, env: Mapping[str, str], windows: bool
) -> str | None:
    """``name`` found on ``path``, absolute components only.

    This replaces :func:`shutil.which` for every shell this repo NAMES, because
    ``which`` searches the CURRENT DIRECTORY first on win32 *even when ``path=``
    is passed explicitly*: read from CPython, the ``os.curdir`` insert sits PAST
    the ``if path is None`` block, in the ``else`` of ``if dirname:`` — the
    branch a bare name always takes — so an explicit ``path=`` does not escape
    it (``shutil.py:1528-1532`` on 3.12.13). It is unconditional on 3.11.15, and
    only from 3.12 is it gated by ``_winapi.NeedCurrentDirectoryForExePath`` —
    and 3.11 is a leg this repo gates on. No longer only read, either: on
    windows-latest, under 3.11 AND 3.12, stock ``which("pwsh", path=<an
    absolute directory holding one>)`` answered ``'.\\pwsh.EXE'`` — the copy
    planted in the cwd, as a relative name (CI run 34238824791). An empty or
    relative ``PATH`` component means ``.`` on every platform besides, which is
    the same hole on macOS and Linux.

    So: skip every component :func:`_is_absolute` rejects, visit each survivor
    once (``os.path.normcase``), and take the first ``name + ext`` under it that
    passes shutil's own access rule — :func:`os.path.exists`, :func:`os.access`
    for ``F_OK | X_OK``, and not a directory. A directory is the clause with
    teeth here: ``_resolve_shell_win32`` has no fall-through, so a directory
    named ``pwsh`` would otherwise become the bash tool's shell outright. Every
    hit is a join onto a ROOTED directory, so no answer can be cwd-relative —
    that is what :func:`_is_absolute`'s drive clause buys, and without it a
    ``PATH`` entry of ``\\evil`` would hand back ``\\evil/sh`` out of the cwd on
    3.11/3.12.

    A missing or empty ``path`` yields no candidate, which is the rule
    :func:`windows_command_shells` already stated for an absent ``PATH`` key.
    Cost, measured on a 25-entry full miss (n=2000): 24.4 µs against
    ``shutil.which``'s 23.7 µs, and 167.9 µs once ``PATHEXT`` multiplies the
    stats — 0.04 % of a pwsh cold start.
    """

    if not path:
        return None
    names = _pathext_names(name, env=env, windows=windows)
    seen: set[str] = set()
    for component in path.split(os.pathsep):
        if not _is_absolute(component):
            continue
        key = os.path.normcase(component)
        if key in seen:
            continue
        seen.add(key)
        for filename in names:
            candidate = os.path.join(component, filename)
            if (
                os.path.exists(candidate)
                and os.access(candidate, os.F_OK | os.X_OK)
                and not os.path.isdir(candidate)
            ):
                return candidate
    return None


def windows_command_shells(
    env: Mapping[str, str],
    *,
    include_posix_sh: bool = False,
    platform: str | None = None,
) -> list[ShellConfig]:
    """Every Windows shell candidate, best first.

    ``[0]`` is what ``_resolve_shell_win32`` has always returned; the list
    exists because a caller that SPAWNS can fall through a candidate a caller
    that only NAMES one cannot. ``include_posix_sh`` inserts the ``sh`` step —
    see this module's header for why exactly one caller takes it.

    ``$SHELL`` is honoured ONLY when it names a file that exists, because the
    common way it is set on Windows is Git-Bash exporting the MSYS path
    ``/usr/bin/bash``, which :class:`subprocess.Popen` cannot spawn. It is also
    the one candidate #241 does not filter: it is the user NAMING a shell, and
    the threat this module defends against is a file in the directory you
    happened to start Aelix in, not an attacker who owns your environment.
    ``%COMSPEC%`` is not in that bucket — it is a stock Windows variable rather
    than an Aelix setting — so a relative one is dropped and the floor below is
    reached instead. That is stricter than CPython, which consults
    ``%SystemRoot%`` only when ``%ComSpec%`` is unset or empty and uses
    ``isabs`` merely to choose ``executable=`` (``subprocess.py:1505-1529``,
    gh-101283); this site cannot inherit that protection, because it NAMES a
    shell and both spawn sites pass ``lpApplicationName = None``.

    The PATH probes read ``env["PATH"]`` and are skipped entirely when the key
    is absent, so a fixture meant as "stock Windows" cannot silently be
    ``sh``-present by falling back to the HOST's ``PATH``. The
    ``_resolve_config`` caller passes :data:`os.environ`, which always has one;
    the bash tool passes its spawn context's env, which a ``spawn_hook`` may
    rewrite, and a PATH-less env there yields ``%COMSPEC%``/``cmd.exe``.

    ``platform`` decides the NAMING rule — ``PATHEXT`` expansion and
    :func:`_env_get`'s fold — and nothing else; it defaults to
    :data:`sys.platform`. It is what :func:`shutil.which` used to branch on
    implicitly, made explicit so the existing tests that drive the win32 CHAIN
    from a POSIX box keep probing with POSIX naming. It is deliberately NOT
    forwarded from ``_resolve_shell(platform=…)`` or
    ``_shell_argv_candidates(platform=…)``: those pick which chain, this picks
    which naming rule. Absoluteness stays outside it (:func:`_is_absolute`).

    The ``%SystemRoot%\\System32\\cmd.exe`` step (#241) sits immediately before
    the bare name, defaulting to ``C:\\Windows`` — the shape and the default
    ``_process_tree.py``'s ``taskkill`` resolution already ships. It diverges
    from that sibling twice, both deliberately. The existence check: that site
    falls through to the bare name after a failed SPAWN, this one only NAMES a
    shell and has no fall-through, so a stale ``%SystemRoot%`` would turn every
    bash tool call into the #104 failure in the very environment where the bare
    ``cmd.exe`` works today. And the default fires on a non-ABSOLUTE
    ``%SystemRoot%``, not merely an absent or empty one (that sibling's ``or``
    is empty-aware, which costs it nothing because a wrong value there just
    retries the bare name): a relative ``winroot`` resolves against the current
    directory, which is the one input this issue exists to distrust, and a floor
    that vanishes with the variable it exists to survive is not a floor. The
    alternative — dropping the candidate, the rule ``%COMSPEC%`` above gets —
    reads the same but is not: ``%COMSPEC%`` has this floor beneath it, while
    beneath this step there is only the bare name, which ``CreateProcess``
    looks for in the application's own directory and then, unless
    ``NoDefaultCurrentDirectoryInExePath`` is set, in the current directory,
    both BEFORE the system directories (Microsoft's documented search order —
    reasoned, not measured here). The bare name stays last because ``[0]`` must
    exist.
    """

    windows = (sys.platform if platform is None else platform) == "win32"
    out: list[ShellConfig] = []
    if (shell := _env_get(env, "SHELL", fold=windows)) and Path(shell).exists():
        out.append(ShellConfig(shell, command_flag_for(shell)))
    path = _env_get(env, "PATH", fold=windows)
    if path is not None:
        if include_posix_sh and (
            found := _which_on_path("sh", path=path, env=env, windows=windows)
        ):
            out.append(ShellConfig(found, POSIX_COMMAND_FLAG))
        for exe in ("pwsh", "powershell"):
            if found := _which_on_path(exe, path=path, env=env, windows=windows):
                out.append(ShellConfig(found, POWERSHELL_COMMAND_FLAG))
    if (comspec := _env_get(env, "COMSPEC", fold=windows)) and _is_absolute(comspec):
        out.append(ShellConfig(comspec, command_flag_for(comspec)))
    system_root = _env_get(env, "SYSTEMROOT", fold=windows) or ""
    if not _is_absolute(system_root):
        system_root = r"C:\Windows"
    system_cmd = os.path.join(system_root, "System32", "cmd.exe")
    if Path(system_cmd).exists():
        out.append(ShellConfig(system_cmd, CMD_COMMAND_FLAG))
    out.append(ShellConfig("cmd.exe", CMD_COMMAND_FLAG))
    return out
