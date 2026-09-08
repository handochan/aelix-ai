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
Since #243 the module owns not only WHICH SHELLS a machine has but also WHICH
SPAWN FAILURES MEAN "not a shell" (:data:`NOT_A_RUNNABLE_SHELL`) — the same
Windows question, and by then it had the same two callers.

HOW A FAMILY IS ASKED FOR UTF-8 OUTPUT is here too (:func:`utf8_output_preamble`,
#239), for the reason ``command_flag_for`` is: it is a fact about the family and
about nothing else, so the alternative is a copy of the table at each caller.
The table has ONE non-empty row — PowerShell. The ``cmd`` row was deleted on
2026-09-09 because the win32 CI leg measured it breaking a real command; that
function's docstring is where the measurement and the decision live.

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
