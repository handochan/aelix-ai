"""``cmd.exe`` dialect classifier (#204 slice 3, ADR-0237).

A hand-written character-level lexer, not a grammar. ``tree-sitter-batch`` 0.11.1
exists on PyPI and was REJECTED, and the ADR records why so the next reader does
not helpfully add it: measured, it needs a trailing newline appended to every
input or the parse is ``has_error=True`` *including bare* ``dir``
(``MISSING "program_token1"``), and a workaround that rewrites the string before
parsing it is a worse foundation for a security gate than 200 lines whose every
branch is testable.

The lexer is character-level for one measured reason (#204 F7): a whitespace
splitter reads ``type nul>C:\\Windows\\System32\\drivers\\etc\\hosts`` as TWO
tokens, ``type`` and ``nul>C:\\Windows\\…``, so the redirect never exists and the
protected-target rule never runs. ``cmd`` needs no space around an operator, so
neither may this.

Three doctrines, all inherited rather than re-invented:

- ADR-0158 strictest-wins composition via :func:`~.common.fold`, whose identity
  is ASK, not ALLOW.
- The ALLOW tier is closed and every row reads its arguments; an unrecognised
  switch is ASK, never ALLOW.
- DENY is reserved for what an agent turn cannot undo. ``del x`` is ASK, the
  same answer ``Remove-Item build\\out.js`` gets on the PowerShell side (#204
  F13) — DENYing an ordinary delete would make AUTO unusable on Windows and
  train click-through.

The bash classifier's DENY is folded in as a FLOOR at the dispatch site
(``permission.py::_auto_classify_bash``), so ``rm -rf /`` and ``find . -delete``
still block under ``cmd`` even though neither name is in a table here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aelix_coding_agent.builtin.bash_classifier import (
    Verdict,
    arguments_are_read_only,
)
from aelix_coding_agent.builtin.shell_classifiers.common import (
    fold,
    is_root_or_protected_target,
    protected_write_target,
    sensitive_read_target,
)
from aelix_coding_agent.builtin.shell_classifiers.dialect import Dialect

# ``cmd`` recognises a switch by its sigil and one following character; a
# ported GNU build accepts ``-x`` for the same switch, so both sigils count
# (the same union ``_SORT_CMD`` uses in ``bash_classifier``).
_SWITCH = re.compile(r"^[/-][A-Za-z0-9?+]")

# Statement separators. ``|`` is the only one that advances the pipeline stage:
# ``&``/``&&``/``||`` and a newline are SEQUENCING, so ``dir & cmd /c del x``
# does not make ``cmd`` an interpreter being fed fetched output while
# ``dir | cmd /c del x`` does.
_SEPARATORS = frozenset({"&", "&&", "||", "|", "\n"})

# The ALLOW tier: 17 names, every one of them gated below. There is no "read-only
# looking name" wildcard and there must not be — ``find`` and ``comp`` were
# dropped from the draft precisely because a name that merely *reads* is not the
# claim the tier makes (#204 F10; cmd's ``find`` is a substring filter of low
# value whose name collides with POSIX ``find``, whose exec/delete DENY is worth
# more than the ALLOW).
#
# ``tree`` was dropped for the reason ``dir``'s allowlist omits ``/s``: it is
# UNCONDITIONALLY recursive, so ``tree /f C:\`` walks the whole drive and hangs
# the turn. Measured ALLOW while ``dir /s /b C:\`` was ASK — the same act, two
# answers, which is how a tier stops describing its own doctrine.
_CMD_ALLOW = frozenset(
    {
        "dir",
        "type",
        "echo",
        "ver",
        "vol",
        "whoami",
        "hostname",
        "where",
        "findstr",
        "more",
        "fc",
        "cd",
        "chdir",
        "set",
        "date",
        "time",
        "sort",
    }
)

# Switch allowlists for the rows whose gate is "these switches and no others".
# ``dir``'s list deliberately omits ``/s`` — a recursive listing of ``C:\`` is
# slow enough to hang the turn — and ``whoami``'s omits ``/all`` for the same
# reason ``Get-ComputerInfo`` is not in the PowerShell ALLOW tier.
_ALLOW_SWITCHES: dict[str, frozenset[str]] = {
    "dir": frozenset(
        {"/a", "/b", "/o", "/p", "/q", "/w", "/x", "/4", "/n", "/c", "/-c", "/l", "/t"}
    ),
    "whoami": frozenset({"/user", "/groups", "/priv"}),
}

# Rows whose gate is "at least one positional and no switch at all". The switch
# ban is the conservative half: ``more /e`` and ``fc /b`` are real and harmless,
# but "never ALLOW an argument we did not read" costs a prompt here and buys the
# rule that every unlisted switch in this dialect is ASK.
_NEEDS_A_POSITIONAL = frozenset({"type", "where", "more", "fc"})

# Rows that take nothing at all. ``hostname.exe`` genuinely has no switches;
# ``ver``/``vol`` have none worth reading.
_BARE_ONLY = frozenset({"ver", "vol", "hostname"})

# Rows whose positionals are PATHS, and therefore the only rows whose positionals
# go through :func:`~.common.sensitive_read_target`. Scoped rather than global
# because that predicate's last clause reads ``<word>:`` as a PowerShell provider
# drive, which is right for ``Get-Content Env:\Path`` and wrong here — measured,
# a global check made ``time 12:00`` an ASK "sensitive read" instead of the DENY
# it is (``12`` parsed as the drive). ``cmd`` has no provider drives.
_READS_A_PATH = frozenset(
    {"dir", "type", "more", "fc", "where", "findstr", "sort", "cd", "chdir"}
)

# Explicit ASK, never an implicit fallthrough. An unknown name ASKs anyway, but
# naming these makes the intent visible and testable the way
# ``_ALWAYS_ASK_COMMANDS`` does on the bash side. Each is a real mutation or a
# real reconnaissance surface with a legitimate agent use, so none is DENY.
#
# ``for`` is here UNCONDITIONALLY. ``for.md``, "Parsing output": a back-quoted
# ``<command>`` between the parentheses "is treated as a command line, which is
# passed to a child Cmd.exe". There is no ``for`` shape worth reasoning about —
# ``for /r %i in (*) do del %i`` is a recursive delete with no ``del /s``
# signature anywhere in it.
#
# ``systeminfo`` is read-only and still here: it dumps domain membership, hotfix
# inventory and network configuration, and it is slow enough to be worth a human
# seeing.
_CMD_ASK = frozenset(
    {
        "for",
        "if",
        "call",
        "goto",
        "start",
        "setlocal",
        "endlocal",
        "pushd",
        "popd",
        "exit",
        "rem",
        "::",
        "assoc",
        "ftype",
        "path",
        "prompt",
        "copy",
        "move",
        "ren",
        "md",
        "mkdir",
        "tasklist",
        "taskkill",
        "net",
        "sc",
        "certutil",
        "bitsadmin",
        "wmic",
        "curl",
        "tar",
        "forfiles",
        "ping",
        "systeminfo",
        "tree",
        "find",
        "comp",
        "mklink",
        "setx",
        "robocopy",
        "xcopy",
        "attrib",
    }
)

# Categorically unrecoverable inside an agent turn: the ``mkfs``/``fdisk`` row
# of ``_DENY_COMMANDS`` transposed to this dialect.
_CMD_DENY = frozenset({"format", "diskpart", "shutdown"})

# ``curl … | sh`` transposed. Dead code unless the stage index is right, which is
# why :func:`_statements` pins it: only ``|`` advances it.
_PIPE_INTO_DENY = frozenset({"cmd", "command", "powershell", "pwsh", "sh", "bash", "wsl"})

# ``reg`` subcommands that WRITE. ``query``/``compare`` read; ``export``/``save``
# write a file rather than the hive, so they are ASK, not DENY.
_REG_WRITE_VERBS = frozenset({"add", "delete", "import", "copy", "restore", "load"})

_ICACLS_DENY_SWITCHES = frozenset(
    {
        "/grant",
        "/deny",
        "/setowner",
        "/reset",
        "/remove",
        "/inheritance",
        "/setintegritylevel",
    }
)
_TAKEOWN_DENY_SWITCHES = frozenset({"/f"})

# Executable extensions ``cmd`` resolves a bare name through. ``shell_basename``
# is not reused for this: it also strips a trailing VERSION suffix, which is
# right for ``bash-5.2`` and wrong for a command name (``net1`` is a real
# Windows binary distinct from ``net``).
_EXECUTABLE_SUFFIXES = (".exe", ".com", ".bat", ".cmd")


@dataclass(frozen=True)
class _Token:
    """One lexed token: the literal it DENOTES, plus how it was spelled.

    ``text`` has quotes removed and ``^`` escapes resolved, because that is what
    ``cmd`` will pass to the program. ``quoted``/``escaped`` survive anyway
    because the command-NAME token is refused when either is set (#204 F11):
    ``de""l x`` and ``d^ir`` are obfuscation, and a gate that silently
    de-obfuscates a name into its ALLOW tier is a gate that can be talked into
    anything.
    """

    text: str
    is_operator: bool = False
    quoted: bool = False
    escaped: bool = False


def _operator_at(command: str, index: int, started: bool) -> str | None:
    """The operator spelled at ``index``, or ``None``.

    ``started`` is whether a word is already being accumulated, and it decides
    only the numbered-handle form: ``echo x 1>y`` redirects handle 1, while the
    ``1`` of ``echo x1>y`` belongs to the word. Both still produce a ``>``
    operator, so the redirect rule fires either way — the distinction only moves
    where the word ends.
    """

    char = command[index]
    if char == "\n":
        return "\n"
    if char == "&":
        return "&&" if command[index + 1 : index + 2] == "&" else "&"
    if char == "|":
        return "||" if command[index + 1 : index + 2] == "|" else "|"
    if char == "<":
        return "<"
    numbered = char.isdigit() and not started and command[index + 1 : index + 2] == ">"
    if char != ">" and not numbered:
        return None
    end = index + 2 if numbered else index + 1
    if command[end : end + 1] == ">":
        end += 1
    elif command[end : end + 1] == "&":
        end += 1
        while command[end : end + 1].isdigit():
            end += 1
    return command[index:end]


def _lex(command: str) -> list[_Token] | None:
    """Character-level lex, or ``None`` when the quoting never closed.

    ``None`` is deliberately whole-command fatal (#204 §D.1): a lexer that lost
    track of quoting has lost track of where the OTHER statements are, so it may
    not claim to have found them all and the caller ASKs for everything.

    ``"…"`` makes its contents literal (an operator inside does not split) and
    ``""`` inside quotes is a literal quote. ``^X`` outside quotes consumes ``X``
    literally, which is what makes ``echo a^&b`` ONE statement, and ``^`` before
    a newline joins the lines.
    """

    tokens: list[_Token] = []
    chars: list[str] = []
    quoted = escaped = started = in_quote = False

    def flush() -> None:
        nonlocal chars, quoted, escaped, started
        if started:
            tokens.append(_Token("".join(chars), False, quoted, escaped))
        chars = []
        quoted = escaped = started = False

    index = 0
    size = len(command)
    while index < size:
        char = command[index]
        if in_quote:
            if char == '"':
                if command[index + 1 : index + 2] == '"':
                    chars.append('"')
                    index += 2
                    continue
                in_quote = False
                index += 1
                continue
            chars.append(char)
            index += 1
            continue
        if char == "^":
            following = command[index + 1 : index + 2]
            if not following:
                index += 1  # a trailing ``^`` escapes nothing
                continue
            if following == "\n":
                index += 2  # line continuation
                continue
            started = escaped = True
            chars.append(following)
            index += 2
            continue
        if char == '"':
            in_quote = quoted = started = True
            index += 1
            continue
        if char in " \t\r":
            flush()
            index += 1
            continue
        operator = _operator_at(command, index, started)
        if operator is not None:
            flush()
            tokens.append(_Token(operator, is_operator=True))
            index += len(operator)
            continue
        started = True
        chars.append(char)
        index += 1
    flush()
    return None if in_quote else tokens


def _statements(tokens: list[_Token]) -> list[tuple[list[_Token], int]]:
    """Split on the separators, carrying each statement's pipeline stage.

    An EMPTY statement (a trailing ``&``) is dropped rather than folded in as an
    ASK: nothing can hide inside it, and folding it would turn ``dir &`` into a
    prompt for a command that is just ``dir``.
    """

    statements: list[tuple[list[_Token], int]] = []
    current: list[_Token] = []
    stage = 0
    for token in tokens:
        if token.is_operator and token.text in _SEPARATORS:
            if current:
                statements.append((current, stage))
            stage = stage + 1 if token.text == "|" else 0
            current = []
            continue
        current.append(token)
    if current:
        statements.append((current, stage))
    return statements


def _split_head(text: str) -> tuple[str, str | None]:
    """A command name and the switch GLUED to it, if any.

    ``cmd`` needs no space between an internal command and its first switch:
    ``dir/s``, ``date/t`` and ``del/q/s x`` all run. Measured before this split
    existed, ``del/s /q C:\\Windows`` resolved to a name of ``s`` (the ``/`` was
    read as a path separator) and left the recursive-delete DENY one character
    behind — the exact shape ``de^l`` is on this module's test list for.

    A path is therefore split on ``\\`` ONLY: in this dialect a ``/`` in the
    first token is a switch sigil. ``C:/Windows/cmd.exe`` consequently resolves
    to the name ``c:``, matches nothing and ASKs, which is the fail-safe answer
    for a spelling ``cmd`` itself would not run.
    """

    name, separator, glued = text.partition("/")
    base = name.rsplit("\\", 1)[-1].casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base, ("/" + glued if separator else None)


def _is_switch(text: str) -> bool:
    return bool(_SWITCH.match(text))


def _switch_keys(text: str) -> list[str]:
    """Every switch a ``/``-sigil token spells, casefolded and value-stripped.

    ``cmd`` switches are case-insensitive (``sort /O`` == ``sort /o``) and take
    an attached value after ``:`` (``dir /a:h``). The list is plural because
    ``/q/s`` is one token to a whitespace splitter and TWO switches to the
    program reading it — splitting on the inner sigil is what keeps ``del /q/s``
    inside the recursive-delete DENY instead of one character outside it.

    A ``-``-sigil token is not split: ``-la`` is a POSIX cluster, not two
    switches, and this dialect has no allowlisted ``-`` switch anyway.

    ``;`` and ``,`` yield an EXTRA key rather than replacing one. cmd documents
    them as standard token delimiters alongside space, tab and ``=``, which
    would make ``del /s;/q C:\\Windows`` spell ``/s`` — measured ASK here, one
    character outside the recursive-delete DENY. That reading is not verified on
    a real Windows box, so it may only ADD keys: every row that ASKed on the
    unsplit spelling still sees the unsplit key and still ASKs, and only the
    DENY rows, which test for a key's PRESENCE, gain.
    """

    lowered = text.casefold()
    if lowered.startswith("-"):
        return [lowered.split(":", 1)[0]]
    keys: list[str] = []
    for part in lowered.split("/"):
        if not part:
            continue
        for piece in (part, *re.split(r"[;,=]", part)):
            key = "/" + piece.split(":", 1)[0]
            if piece and key not in keys:
                keys.append(key)
    return keys


def _keys_of(tokens: list[_Token]) -> list[str]:
    keys: list[str] = []
    for token in tokens:
        if _is_switch(token.text):
            keys.extend(_switch_keys(token.text))
    return keys


def _switch_value(tokens: list[_Token], position: int) -> str | None:
    """The value of the switch at ``position``: attached after ``:``, else next.

    ``sort /o out.txt`` and ``sort /o:out.txt`` name the same file and cmd
    accepts both spellings.
    """

    _, _, attached = tokens[position].text.partition(":")
    if attached:
        return attached
    for token in tokens[position + 1 :]:
        if not _is_switch(token.text):
            return token.text
    return None


def _redirect_verdicts(tokens: list[_Token]) -> list[Verdict]:
    """One verdict per redirect operator; DENY only on a protected target.

    ANY redirect drops a statement out of the ALLOW tier before the name table
    is consulted (#204 F5/F8), which is why there is no per-row "no redirect"
    clause anywhere below and why every future ALLOW row inherits the rule.

    ``2>&1`` and ``<`` have no writable target: ``2>&1`` names a handle and ``<``
    names a SOURCE. Both are ASK, and for ``<`` that ASK subsumes §D.4's
    sensitive-read check on the source — the check could only produce the same
    answer, so it is stated here rather than written as a branch that can never
    change a verdict.
    """

    verdicts: list[Verdict] = []
    for position, token in enumerate(tokens):
        if not token.is_operator:
            continue
        if "<" in token.text or "&" in token.text:
            verdicts.append(Verdict.ASK)
            continue
        following = tokens[position + 1] if position + 1 < len(tokens) else None
        target = None if following is None or following.is_operator else following.text
        verdicts.append(Verdict.DENY if protected_write_target(target) else Verdict.ASK)
    return verdicts


def _arguments(tokens: list[_Token]) -> list[_Token]:
    """The statement's arguments: no operators, and no redirect TARGETS.

    Excluding the target matters for the positional gates — ``type nul > out.txt``
    must not read ``out.txt`` as a second positional of ``type``.
    """

    arguments: list[_Token] = []
    skip = False
    for token in tokens[1:]:
        if token.is_operator:
            # ``2>&1`` ends in a handle digit and consumes no word; every other
            # redirect takes the next word as its target.
            skip = token.text[-1] in "><"
            continue
        if skip:
            skip = False
            continue
        arguments.append(token)
    return arguments


def _unreadable(tokens: list[_Token]) -> bool:
    """Whether this statement contains something the lexer refuses to interpret.

    A TRUE here floors the statement at ASK; it does not skip the DENY tables.
    That is the difference between "we could not read this" and "this is fine":
    ``icacls C:\\Windows /deny Everyone:(F)`` carries parentheses AND retargets a
    protected tree, and #204 §G pins it DENY. An early ASK return would have
    lost it, so the unreadable conditions raise the floor and the tables still
    run — which is strictly stricter than either rule alone.

    ``%VAR%`` and ``!VAR!`` are here because this classifier never expands a
    variable (the same rule ``bash_classifier`` states for ``$VAR`` redirect
    targets), and ``(``/``)`` because a parenthesised block is a compound
    statement this lexer does not split.
    """

    return any(char in token.text for token in tokens for char in "%!()")


def _allow_verdict(name: str, arguments: list[_Token], tokens: list[_Token]) -> Verdict:
    """The ALLOW tier's per-row gate. Anything unlisted is ASK, never ALLOW."""

    positionals = [token for token in arguments if not _is_switch(token.text)]
    verdict = _row_verdict(name, arguments, tokens)
    # "Read-only" is not the same claim as "harmless to read" — the gap
    # ``type C:\Users\me\.ssh\id_rsa`` walks through (#204 F9). Applied only to a
    # row that was about to ALLOW: a DENY row has already decided, and raising a
    # DENY to ASK on a credential path would be exactly backwards.
    if (
        verdict is Verdict.ALLOW
        and name in _READS_A_PATH
        and any(sensitive_read_target(token.text) for token in positionals)
    ):
        return Verdict.ASK
    return verdict


def _row_verdict(name: str, arguments: list[_Token], tokens: list[_Token]) -> Verdict:
    """The per-name half of the ALLOW tier, before the sensitive-read gate."""

    switches = [token for token in arguments if _is_switch(token.text)]
    positionals = [token for token in arguments if not _is_switch(token.text)]
    keys = _keys_of(arguments)

    if name in _BARE_ONLY:
        return Verdict.ALLOW if not arguments else Verdict.ASK
    if name in _ALLOW_SWITCHES:
        allowed = _ALLOW_SWITCHES[name]
        if any(key not in allowed for key in keys):
            return Verdict.ASK
        return Verdict.ASK if name == "whoami" and positionals else Verdict.ALLOW
    if name in _NEEDS_A_POSITIONAL:
        return Verdict.ALLOW if positionals and not switches else Verdict.ASK
    if name == "echo":
        # ``echo`` writes to stdout and nowhere else; the redirect rule already
        # took the only shape that writes a file.
        return Verdict.ALLOW
    if name == "findstr":
        # ``/f:`` reads a file LIST and ``/g:`` reads the pattern from a file;
        # both turn one argument into an unbounded set of reads. ``/s`` is the
        # same hazard by a third route and was measured escaping the
        # sensitive-read gate entirely: ``findstr /s /i password C:\*`` and
        # ``findstr /s . C:\Users\*`` were ALLOW — a recursive grep of the whole
        # drive for a credential, auto-run. It is also ``dir /s``'s hang hazard,
        # which this tier already refuses one row up.
        return Verdict.ASK if {"/f", "/g", "/s"} & set(keys) else Verdict.ALLOW
    if name in ("cd", "chdir"):
        return Verdict.ALLOW if not switches and len(positionals) <= 1 else Verdict.ASK
    if name == "set":
        # Bare ``set`` dumps every environment variable, which is the exfil
        # argument that excluded ``Get-Variable *`` on the PowerShell side, and
        # ``set FOO=bar`` assigns. Only the one-name QUERY form is read-only.
        if any("=" in token.text for token in tokens):
            return Verdict.ASK
        return Verdict.ALLOW if len(positionals) == 1 and not switches else Verdict.ASK
    if name in ("date", "time"):
        return _clock_verdict(keys, positionals)
    if name == "sort":
        return _sort_verdict(arguments)
    return Verdict.ASK


def _clock_verdict(keys: list[str], positionals: list[_Token]) -> Verdict:
    """``date``/``time``: ``/t`` displays, a positional SETS, bare one PROMPTS.

    ``date.md``: bare ``date`` "displays the current system date setting and
    **prompts you to enter a new date**" — on a non-tty that blocks the AUTO
    session forever, the identical hazard that bars ``Start-Sleep 999999``. And
    ``date 01-01-2030`` sets the clock, which breaks TLS validation, scheduled
    tasks and every timestamp on the box for the rest of the session; that is
    the DENY. Both were measured ALLOW before ``_READ_ONLY_SWITCHES`` (#204 §0).
    """

    if positionals:
        return Verdict.DENY
    if not keys:
        return Verdict.ASK
    return Verdict.ALLOW if all(key == "/t" for key in keys) else Verdict.ASK


def _sort_verdict(arguments: list[_Token]) -> Verdict:
    """``sort.exe``: ``/O`` names an output file and ``/T`` a temp directory.

    Neither produces a redirect operator for :func:`_redirect_verdicts` to see —
    ``sort in.txt /o out.txt`` writes a file with no ``>`` anywhere, which is the
    whole reason it is a pinned ASK in ``test_bash_classifier.py``. So the switch
    VALUE is run through the same protected-target predicate a real redirect
    target gets: overwriting ``C:\\Windows\\y`` is DENY, overwriting ``out.txt``
    is the same ASK ``echo x > out.txt`` gets.

    Everything else defers to the CMD row of ``bash_classifier``'s
    ``_READ_ONLY_SWITCHES``, so the two callers of that table cannot drift.
    """

    for position, token in enumerate(arguments):
        if not _is_switch(token.text):
            continue
        if not {"/o", "/t"} & set(_switch_keys(token.text)):
            continue
        target = _switch_value(arguments, position)
        return Verdict.DENY if protected_write_target(target) else Verdict.ASK
    tokens: list[str | None] = [token.text for token in arguments]
    if arguments_are_read_only("sort", tokens, Dialect.CMD):
        return Verdict.ALLOW
    return Verdict.ASK


def _reg_verdict(arguments: list[_Token]) -> Verdict:
    """``reg``: a write verb aimed at ``HKLM`` is machine-wide and DENY.

    ``query``/``compare`` read. ``export``/``save`` write a FILE rather than the
    hive, so they are ASK — the redirect rule's answer for the same act.
    """

    positionals = [token for token in arguments if not _is_switch(token.text)]
    if not positionals or positionals[0].text.casefold() not in _REG_WRITE_VERBS:
        return Verdict.ASK
    if any(token.text.casefold().startswith("hklm") for token in positionals[1:]):
        return Verdict.DENY
    return Verdict.ASK


def _acl_verdict(name: str, arguments: list[_Token]) -> Verdict:
    """``takeown``/``icacls`` — DENY only when they retarget a protected tree.

    #204 F15. ``takeown /r`` is NOT required: ``takeown /f C:\\Windows`` already
    hands the directory itself to the caller, and requiring the recursive flag
    made the rule one character evadable.
    """

    switches = _TAKEOWN_DENY_SWITCHES if name == "takeown" else _ICACLS_DENY_SWITCHES
    if not set(_keys_of(arguments)) & switches:
        return Verdict.ASK
    targets = [token.text for token in arguments if not _is_switch(token.text)]
    if any(is_root_or_protected_target(target) for target in targets):
        return Verdict.DENY
    return Verdict.ASK


def _name_verdict(name: str, arguments: list[_Token], tokens: list[_Token], stage: int) -> Verdict:
    """The tier lookup, in the order the tiers are checked.

    DENY rows first and unconditionally, so an obfuscated NAME cannot buy its way
    past them: :func:`_unreadable` floors ``de^l /s /q C:\\Windows`` at ASK, and
    this still resolves it to ``del`` and returns DENY, which the fold keeps.
    """

    keys = _keys_of(arguments)
    if stage > 0 and name in _PIPE_INTO_DENY:
        return Verdict.DENY
    if name in _CMD_DENY:
        return Verdict.DENY
    if name in ("del", "erase", "rd", "rmdir"):
        # #204 F13: WITHOUT ``/s`` this is ASK, the same answer
        # ``Remove-Item build\out.js`` gets. The pair has to be consistent or the
        # gate teaches that one spelling of a delete is worse than the other.
        return Verdict.DENY if "/s" in keys else Verdict.ASK
    if name == "cipher":
        # ``/w`` overwrites free space in place; there is nothing to undo it.
        return Verdict.DENY if "/w" in keys else Verdict.ASK
    if name == "schtasks":
        return Verdict.DENY if "/create" in keys else Verdict.ASK
    if name == "reg":
        return _reg_verdict(arguments)
    if name in ("takeown", "icacls"):
        return _acl_verdict(name, arguments)
    if name in _CMD_ASK:
        return Verdict.ASK
    if name in _CMD_ALLOW:
        return _allow_verdict(name, arguments, tokens)
    return Verdict.ASK


def _ungrouped(tokens: list[_Token]) -> list[_Token]:
    """The statement with its grouping parentheses stripped off the words.

    ``(`` and ``)`` are cmd's block syntax and never part of a name or a path,
    but the lexer accumulates them into the adjacent word. Measured on this
    branch before this ran: ``(del /s x)`` and ``(dir) & (del /s x)`` were ASK
    while ``del /s x`` alone is DENY, because :func:`_split_head` resolved the
    name ``(del``, which is in no table — the DENY row was never reached.

    This changes the NAME lookup only. :func:`_unreadable` still sees the
    original tokens and still floors a parenthesised statement at ASK, so
    ``(dir)`` stays ASK and this can only ever RAISE a verdict.
    """

    stripped: list[_Token] = []
    for token in tokens:
        if token.is_operator:
            stripped.append(token)
            continue
        text = token.text.strip("()")
        if text:
            stripped.append(_Token(text, False, token.quoted, token.escaped))
    return stripped


def _classify_statement(tokens: list[_Token], stage: int) -> Verdict:
    """One statement's verdict: the name's answer, raised by every other rule."""

    if not tokens:
        return Verdict.ASK
    if tokens[0].is_operator:
        # ``> out.txt dir`` — a leading redirect is legal cmd and there is no
        # first-token name to look up (#204 F28).
        return Verdict.ASK
    floor = (
        Verdict.ASK
        if tokens[0].quoted or tokens[0].escaped or _unreadable(tokens)
        else Verdict.ALLOW
    )
    words = _ungrouped(tokens)
    if not words or words[0].is_operator:
        return Verdict.ASK
    head = words[0]
    # ``@`` suppresses echoing of the line and says nothing about what it runs.
    name, glued = _split_head(head.text.lstrip("@"))
    if not name:
        return Verdict.ASK
    arguments = _arguments(words)
    if glued is not None:
        arguments = [_Token(glued), *arguments]
    return fold(
        [floor, _name_verdict(name, arguments, words, stage), *_redirect_verdicts(words)]
    )


def classify_cmd(command: str) -> Verdict:
    """Classify ``command`` as ``cmd.exe`` syntax (fail-safe to ASK, never raises).

    Mis-splitting always errs strict, which is why a hand-written lexer is
    acceptable here at all (#204 §E). An OVER-split produces more statements,
    each classified with unknown→ASK, and the fold takes the max. An UNDER-split
    hands a longer argument list to a known ALLOW name, and every ALLOW row above
    ASKs on an unrecognised argument. Neither direction can manufacture an ALLOW.
    """

    if not command or not command.strip():
        return Verdict.ASK
    try:
        tokens = _lex(command)
        if tokens is None:
            return Verdict.ASK
        return fold(
            _classify_statement(statement, stage)
            for statement, stage in _statements(tokens)
        )
    except Exception:  # noqa: BLE001 — any lexer failure → ASK (safe)
        return Verdict.ASK


__all__ = ["classify_cmd"]
