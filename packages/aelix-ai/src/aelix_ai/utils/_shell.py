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
Since #243 the module owns not only WHICH SHELLS a machine has but also WHICH
SPAWN FAILURES MEAN "not a shell" (:data:`NOT_A_RUNNABLE_SHELL`) — the same
Windows question, and by then it had the same two callers.

HOW A FAMILY IS ASKED FOR UTF-8 OUTPUT is here too (:func:`utf8_output_preamble`,
#239), for the reason ``command_flag_for`` is: it is a fact about the family and
about nothing else, so the alternative is a copy of the table at each caller.
The table has ONE non-empty row — PowerShell. The ``cmd`` row was deleted on
2026-09-09 because the win32 CI leg measured it breaking a real command; that
function's docstring is where the measurement and the decision live.

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
stays with the caller — and WHETHER to prepend the preamble at all is such a
policy: the bash tool does, a ``!command`` does not, because its stdout is a
credential returned verbatim. ``_resolve_config`` adds ``-NoProfile`` and
``-NonInteractive`` to the PowerShell family and ``/d /s`` to the ``cmd`` one,
because a credential command must not run the user's profile — measured on
PowerShell 7, a profile that writes to stdout is prepended to the resolved key
(``profile-banner\\nsk-KEY``) and an unguarded ``Read-Host`` prompt lands inside
it (``'give me a key: \\nGOT:'`` against ``'GOT:'``). The bash tool runs the
user's interactive shell and must keep their profile, so it adds none of that.
"""

from __future__ import annotations

import errno
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

#: Spawn failures that mean "this candidate is not a runnable shell".
#: Classified by ERRNO and not by exception class: CPython maps a win32 spawn
#: failure to an errno through ``PC/errmap.h`` BEFORE ``OSError``'s subclass
#: table is consulted, and that table has no ``ENOEXEC`` and no ``EINVAL``
#: entry — so a ``sh.cmd`` / ``pwsh.bat`` / non-PE ``$SHELL``
#: (``ERROR_BAD_EXE_FORMAT`` 193, with 11 and 188..202) and everything falling
#: to ``errmap.h``'s ``default: return EINVAL`` arrive as a BARE ``OSError``.
#: Catching by class would abort ``_resolve_config``'s chain before the
#: ``cmd.exe`` floor and leave ``!command`` exactly as dead as #227 found it.
#:
#: TWO READINGS, ONE SET. ``_resolve_config`` walks a chain, so a member there
#: means "try the next candidate"; ``tools/bash.py`` (#243) resolves exactly one
#: shell, so there it means "there is no next candidate — report the
#: ``[bash] failed to spawn`` line and exit 127". Same predicate, different
#: terminal action; do not "fix" one site into the other, and do not fork the
#: set by subtraction at either — #227 measured that dropping five members from
#: a copy was invisible to the whole suite (10268 passed).
#:
#: THE COST OF ``EINVAL``, stated rather than avoided: it is where ``errmap.h``'s
#: ``default:`` arm sends every winerror the table does not name, so at the bash
#: site a host failure like ``ERROR_ELEVATION_REQUIRED`` (740) or
#: ``ERROR_NO_SYSTEM_RESOURCES`` (1450) is reported as exit 127 on win32.
#: Accepted, because the alternative is worse: an escape from that site reaches
#: ``tui/shell.py``'s ``_input_loop``, which catches only ``EOFError``, so it
#: takes the whole session down.
#:
#: ``EMFILE``/``ENOMEM``/``EBADF`` are deliberately outside it at BOTH sites, and
#: so is an ``OSError`` carrying no errno at all (``None not in
#: frozenset[int]``): they are process-resource failures the next candidate
#: cannot fix and no verdict on the shell, so a ``!command`` keeps today's
#: ``None`` after one spawn and the bash tool re-raises rather than claiming a
#: 127 a model would read as "command not found". A ``winerror`` allowlist was
#: rejected — ``exc.winerror`` fails the host pyright leg while ``exc.errno`` is
#: clean on both. ``ELOOP`` is inert on win32 and correct on POSIX.
NOT_A_RUNNABLE_SHELL = frozenset(
    {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EACCES,
        errno.EPERM,
        errno.ENOEXEC,
        errno.ELOOP,
        errno.EINVAL,
    }
)


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


# The .NET setter, not PowerShell's own ``$OutputEncoding``: it calls
# ``SetConsoleOutputCP``, so NATIVE children (git, uv) sharing the console
# follow it too, which is the whole point. ``UTF8Encoding::new($false)`` is the
# BOM-less overload — the default constructor emits a preamble. ``try{}catch{}``
# because a console-less host can throw there and a preamble must never fail
# the user's command; it prints nothing on either path.
POWERSHELL_UTF8_PREAMBLE = "try{[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)}catch{};"


def utf8_output_preamble(shell: str) -> str:
    """One statement to prepend so ``shell`` writes UTF-8, or ``""`` (#239).

    Exactly one family takes one: PowerShell. Everything else gets ``""``, for
    two different reasons.

    POSIX gets ``""`` because those children already speak UTF-8 by locale, and
    exporting ``LC_ALL`` would be a behaviour change with no bug behind it.

    ``cmd`` GETS ``""`` BECAUSE ITS PREAMBLE BROKE A REAL COMMAND. Until
    2026-09-09 this arm returned ``chcp 65001 >nul&``. The win32-only probe
    ``tests/tools/test_bash_utf8_preamble.py::
    test_win32_the_cmd_arm_does_not_break_a_spaced_executable_path`` was written
    by #239's cross-review precisely because nobody here can run Windows, and it
    fired on its FIRST execution — CI run 34272507388, windows-latest, py3.11
    and py3.12 both, the ``[bare]`` parameter only::

        'C:\\Users\\runneradmin\\AppData\\Local\\Temp\\…\\a' is not recognized as
        an internal or external command, operable program or batch file.

    The mechanism had been read off ``cmd /?`` before the leg confirmed it.
    ``_LocalBashOperations.exec`` hands :class:`subprocess.Popen` a LIST, so
    :func:`subprocess.list2cmdline` renders ``cmd.exe /c "C:\\a dir\\probe.exe"``
    — exactly two quote characters, no ``&<>()@^|`` between them, whitespace
    between them, and the text between them the name of an executable file.
    That is ``cmd /c``'s rule 1, and it means ``cmd`` KEEPS the quotes.
    A preamble puts ``&`` and ``>`` inside them, rule 1 stops applying, rule 2
    strips them, and ``C:\\…\\a`` is what ``cmd`` then tries to run.

    NO SPELLING OF A ``cmd`` PREAMBLE SURVIVES THAT RULE, which is why the arm
    is deleted rather than re-quoted. Rule 1 requires the WHOLE text between the
    two quotes to be the name of an executable file, so it is not the ``&`` and
    the ``>`` that disqualify it — it is having a prefix at all. Dropping the
    ``>nul``, using a newline instead of ``&``, wrapping in ``call``: each still
    leaves text in front of the path. And the command cannot be quoted from
    here either, because what arrives is one opaque string the model wrote.

    THE LEG ALSO SETTLED THE TWO SHAPES THAT DO NOT CHANGE. The probe's other
    two parameters did not fail on that run (each carries a ``subprocess.run``
    control and skips rather than fails when the shape was already broken
    without a preamble): an unquoted path WITH an argument, where the text
    between the quotes is not the name of an executable file so rule 1 had
    already failed and rule 2 had already stripped them; and a path the model
    quoted itself, which ``list2cmdline`` escapes as ``\\"`` so it arrives with
    FOUR quote characters and rule 1 never applied. The cross-review's own case
    was that second one, and it is REFUTED. The preamble had exactly one victim.

    "STOPS RESOLVING" DID NOT OVERSTATE IT — the other thing the leg settled.
    This docstring used to argue that ``CreateProcess``'s successive-token
    search (``C:\\Program.exe``, then ``C:\\Program Files\\Git.exe``, …) would
    still launch the binary out of the residue. It never gets the chance:
    ``cmd`` resolves the command name itself and answers "is not recognized"
    first, and the command does not run.

    WHAT REPLACES IT is
    :func:`aelix_ai.utils._child_output.decode_child_output`, which is what #239
    is actually about — it reads a child's console-code-page output without
    being told the page in advance. The preamble was always a NARROWING and
    never a guarantee: it cannot cover a native child that ignores the console
    page, and PowerShell parses the entire ``-Command`` script before executing
    any statement, so a parse error discards even the surviving arm unexecuted
    (measured on pwsh 7.6.5, ``-Command 'Write-Output "PREAMBLE-RAN"; echo a |
    | echo b'`` prints the ``ParserError`` and never ``PREAMBLE-RAN``). #239's
    report is that exact shape (``uv --version && uv cache dir`` →
    ``InvalidEndOfLine``).

    THE PRICE, STATED. A ``cmd`` child that would have emitted UTF-8 now emits
    the console page, so it takes the decoder's route and with it that route's
    documented DBCS ambiguity (ADR-0238). On a Western box it is a real loss and
    not only a shift: with no DBCS page in the chain the decoder is
    ``errors="replace"`` byte for byte, so an OEM-page character that the
    deleted ``chcp 65001`` would have turned into UTF-8 now reads ``U+FFFD``.
    THAT IS A LOSS AGAINST THE INTERMEDIATE BUILD THAT CARRIED THE ARM, NOT
    AGAINST THE PREVIOUS RELEASE: ``0.1.0-beta.1`` decoded every child
    ``utf-8``/``errors="replace"``, so those same bytes read ``U+FFFD`` there
    too. Measured on this tree: for a cp850 buffer holding the German
    ``Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}\N{LATIN SMALL LETTER SHARP S}e``,
    ``decode_child_output(buf, fallbacks=("cp850",))`` equals ``buf.decode(
    "utf-8", errors="replace")``, both spelling the two accented bytes
    ``U+FFFD``; over 16 accented German/French console lines, 16 of 16 equal
    and 16 of 16 marked on both sides. Paid because one mojibake line is worth
    less than a command that does not run, and because the arm is narrow —
    ``_resolve_shell_win32`` takes ``windows_command_shells(env)[0]`` and
    ``powershell.exe`` is on a stock Windows PATH, so a box that reaches
    ``cmd`` at all did one of four things: set an explicit ``shell_path``; set
    ``$SHELL`` to an existing
    ``cmd.exe`` (built BEFORE any PATH probe, so it wins even
    with ``pwsh`` on PATH); handed this a spawn-context env with no ``PATH``
    key at all, which skips the probes and leaves ``%COMSPEC%``/``cmd.exe`` —
    a ``spawn_hook`` can produce exactly that, as ``windows_command_shells``'s
    own docstring records; or has a ``PATH`` with no PowerShell on it, System32
    stripped being the usual way. The last three are measured on darwin against
    ``windows_command_shells``; the ``shell_path`` route never reaches it —
    ``_resolve_shell`` validates the setting and returns it.

    The PowerShell arm is untouched by any of this. Its statement goes into a
    script ``pwsh`` parses itself, not into a string ``cmd`` re-quotes, and
    ``test_win32_powershell_51_parses_the_preamble`` did not fail on the same
    leg — which is as much as a ``-q`` run can say, since that case skips when
    the runner has no 5.1 under ``%SYSTEMROOT%``.

    ``command.com`` never reached the ``cmd`` row even while there was one, for
    the reason ``_shell_argv`` records: :func:`shell_basename` strips only
    ``.exe``, so the name never matches. Pre-existing (#104), and now moot here.
    """

    if shell_basename(shell) in POWERSHELL_NAMES:
        return POWERSHELL_UTF8_PREAMBLE
    return ""


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
