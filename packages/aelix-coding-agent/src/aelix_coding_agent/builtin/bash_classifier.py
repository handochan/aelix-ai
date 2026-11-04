"""Tree-sitter-bash AUTO-mode safety classifier (WP-0 STEP 7, ADR-0158).

Pure, dependency-injected :func:`classify` that maps a shell command to a
three-level :class:`Verdict` (``ALLOW`` < ``ASK`` < ``DENY``) by walking the
tree-sitter-bash AST. AST classification is structurally more sound than the
regex :class:`~aelix_coding_agent.builtin.guardrail.GuardrailExtension` against
quoting / subshell / concatenation evasions, e.g.:

- ``echo "rm -rf /"`` → ALLOW (the ``rm`` is a quoted string, not a command),
- ``$(echo rm) -rf /`` → ASK (the command name is dynamic),
- ``r''m -rf /`` → DENY (the concatenation resolves to ``rm``).

SECURITY: every uncertainty path returns ASK, never ALLOW —
- the grammar import failing at module load → a fallback that ASKs for *every*
  command (the agent still runs, just without auto-allow),
- ``root_node.has_error`` (malformed/partial input) → ASK,
- an unrecognized node structure / dynamic command name → ASK.

The verdict drives ONLY the AUTO posture's allow/ask/deny. The regex Guardrail
remains the first-block-wins floor (defense in depth): this classifier does NOT
relax it.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from aelix_coding_agent.builtin.shell_classifiers.dialect import Dialect


class Verdict(IntEnum):
    """Three-level classification; ordered so ``max`` picks the worst.

    ``ALLOW`` (0) < ``ASK`` (1) < ``DENY`` (2). A pipeline / list / subshell
    bubbles up the MAX (worst) verdict of its parts — one dangerous stage taints
    the whole command.
    """

    ALLOW = 0
    ASK = 1
    DENY = 2


# Commands that are categorically dangerous → DENY.
_DENY_COMMANDS = frozenset(
    {"rm", "dd", "mkfs", "shred", "fdisk", "sudo", "doas"}
)

# Filesystem/permission mutators that are safe in the read-only case but become
# destructive with a recursive flag on ``/`` or ``~`` (e.g. ``chmod -R 777 /``,
# ``chown -R``). They are NOT in ``_READ_ONLY``: a recursive form targeting a
# root/home path → DENY, anything else → ASK (never silent-ALLOW). See
# :func:`_classify_recursive_mutator`.
_RECURSIVE_MUTATORS = frozenset(
    {"chmod", "chown", "chgrp", "mv", "cp", "truncate", "tee", "ln"}
)

# System-state mutators that always prompt (never auto-allow); listed so the
# AUTO floor never silent-allows them via the unknown-command ASK fallthrough.
# They resolve to ASK today; kept explicit so the intent is visible/tested.
_ALWAYS_ASK_COMMANDS = frozenset(
    {"mount", "umount", "kill", "pkill", "killall", "iptables", "nft"}
)

# ``find`` / ``fd`` argument flags that EXECUTE or DELETE — their presence turns
# an otherwise read-only traversal into arbitrary command execution / recursive
# deletion → DENY.
_FIND_EXEC_FLAGS = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprintf", "-fls", "-fprint", "-fprint0"}
)
_FD_EXEC_FLAGS = frozenset({"-x", "--exec", "-X", "--exec-batch"})

# Shells that, when a pipeline pipes INTO them, can execute arbitrary fetched
# code (``curl … | sh``) → DENY at any non-first stage.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})

# Shell basenames the bundled tree-sitter BASH grammar genuinely describes.
# The other POSIX shells differ from bash in ways that do not move a COMMAND
# NAME, which is all this classifier reads, so they stay in.
#
# ``fish`` is deliberately OUT (its syntax diverges far enough that the
# extracted name can be wrong), and so is every Windows shell (#104).
#
# The reason, CORRECTED — the example this comment carried for a long time is
# false, and it was copied into #204, ``permission.py``, two test docstrings
# and two specs. ``Remove-Item -Recurse -Force C:\`` and ``del /s /q C:\Windows``
# do NOT yield ALLOW: they match no table, so they fall through to the
# unknown-command ASK below. Measured, not assumed.
#
# The mis-permissioning is real but it is elsewhere, and it is the opposite
# shape — not an unknown destructive NAME, but a KNOWN read-only name whose
# arguments nobody reads. ``date 01-01-2030`` sets the clock under cmd,
# ``sort in.txt /o out.txt`` writes a file with no redirect node. Both measured
# ALLOW before ``_READ_ONLY_SWITCHES`` (#204) started reading their arguments
# with the dialect's own switch syntax. So the ALLOW tier is where a dialect
# actually changes the answer, and gating the shell is a stopgap for that, not
# for a name the grammar never claimed to know.
#
# #204 / ADR-0237 ended the stopgap for the two Windows dialects: they are still
# OUT of this set, but the gate no longer REACHES this set for them. A resolved
# ``powershell``/``pwsh``/``cmd`` takes the dialect branch in
# ``permission.py``'s ``_auto_classify_bash`` and is answered by
# ``shell_classifiers/{powershell,cmd}.py`` with this classifier's DENY kept as
# a floor. ``fish`` still has no grammar of its own, so it is the one shell for
# which "out of this set" still means the downgrade.
_CLASSIFIABLE_SHELLS = frozenset({"bash", "sh", "dash", "ksh", "mksh", "zsh"})


def is_classifiable_shell(shell: str) -> bool:
    """Whether :func:`classify`'s verdict is meaningful for ``shell``.

    ``shell`` is a path (``/bin/bash``, ``C:\\…\\powershell.exe``) or a bare
    name. ``False`` must force ASK at the permission gate: for a shell outside
    this set the verdict is not merely unknown, it is misleading.

    Since #204 / ADR-0237 the gate asks this only on its POSIX/UNKNOWN branch —
    ``powershell``/``pwsh``/``cmd`` never get here, because they are classified
    by their own dialect module rather than downgraded. In practice ``fish`` and
    an unresolvable shell are what this now answers ``False`` for.
    """

    from aelix_ai.utils._shell import shell_basename  # noqa: PLC0415

    return shell_basename(shell) in _CLASSIFIABLE_SHELLS


# Read-only commands that are safe to auto-run → ALLOW.
_READ_ONLY = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "echo",
        "pwd",
        "whoami",
        "id",
        "date",
        "env",
        "printenv",
        "uname",
        "hostname",
        "which",
        "type",
        "file",
        "stat",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "tree",
        "du",
        "df",
        "ps",
        "top",
        "uptime",
        "cut",
        "sort",
        "uniq",
        "diff",
        "cmp",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "true",
        "false",
        "test",
        "sleep",
        "jq",
        "yq",
        "column",
        "nl",
        "tac",
    }
)

# git subcommands that only read state → ALLOW; anything else → ASK.
_GIT_READ_ONLY = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "branch",
        "remote",
        "config",
        "rev-parse",
        "describe",
        "blame",
        "shortlog",
        "tag",
        "ls-files",
        "ls-remote",
        "for-each-ref",
        "cat-file",
        "reflog",
        "whatchanged",
        "name-rev",
        "rev-list",
    }
)

# Write-redirect into one of these (a path that starts with / lives under) →
# DENY; any other write-redirect → ASK. Includes home dotfiles / shell-rc / cron
# so a redirect targeting a persistence/backdoor surface is hard-denied (finding
# WP-0 #5). Note: a ``$HOME``/``$VAR`` redirect target is dynamic and resolves to
# ASK by design (we never expand env-vars — the safe direction).
_PROTECTED_WRITE_PREFIXES = (
    "/etc",
    "/dev",
    "/boot",
    "/sys",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/root",
    "~/.ssh",
    "/.git",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.config",
    "~/.local",
    "/var/spool/cron",
)

# Control-flow node types → ASK (we don't statically reason about branches).
_CONTROL_FLOW = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "case_statement",
        "function_definition",
        "c_style_for_statement",
    }
)

# Statement-level assignments → ASK (#657 decision 3, review round 1). The four
# node types tree-sitter-bash gives ``X=1``, ``A=1 B=2``, ``export`` /
# ``declare`` / ``typeset`` / ``local`` / ``readonly`` and ``unset``.
_ASSIGNMENT_STATEMENTS = frozenset(
    {"variable_assignment", "variable_assignments", "declaration_command", "unset_command"}
)


class _GrammarUnavailable(Exception):
    """Raised internally when the tree-sitter grammar could not be loaded."""


def _load_parser() -> Any:
    """Build a tree-sitter ``Parser`` for bash, or raise :class:`_GrammarUnavailable`.

    Wrapped so an ImportError / ABI mismatch / any load failure becomes the
    fail-safe (ASK-everything) path rather than crashing the gate.
    """

    try:
        import tree_sitter_bash as tsb  # noqa: PLC0415 — optional C-ext, lazy
        from tree_sitter import Language, Parser  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — any import problem → fail safe
        raise _GrammarUnavailable(str(exc)) from exc
    try:
        language = Language(tsb.language())
        return Parser(language)
    except Exception as exc:  # noqa: BLE001 — ABI / construction failure
        raise _GrammarUnavailable(str(exc)) from exc


# Build ONCE at import. On any failure ``_PARSER`` stays None and every
# :func:`classify` call returns ASK (fail-safe), so the agent still runs in AUTO
# mode — it just never auto-allows.
_PARSER: Any | None
try:
    _PARSER = _load_parser()
except _GrammarUnavailable:
    _PARSER = None


def _node_literal(node: Any) -> str | None:
    """Resolve a node to a static literal string, or ``None`` if it is dynamic.

    - ``word`` → its text.
    - ``string`` → the joined ``string_content`` children (a fully-literal
      double-quoted string; an embedded expansion makes it dynamic → ``None``).
    - ``raw_string`` → the single-quoted body verbatim.
    - ``concatenation`` → concatenate each part if EVERY part resolves
      (e.g. ``r''m`` → ``rm``); a dynamic part poisons the whole → ``None``.
    - ``command_substitution`` / ``simple_expansion`` / ``expansion`` →
      dynamic → ``None`` (the value is unknown until runtime).
    """

    t = node.type
    if t in ("word", "number"):
        # ``number`` is a static literal (e.g. the ``777`` mode in ``chmod 777``)
        # — resolving it keeps an arg-scan from spuriously bailing to ASK.
        return node.text.decode(errors="replace")
    if t == "raw_string":
        text = node.text.decode(errors="replace")
        # Strip the surrounding single quotes.
        if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
            return text[1:-1]
        return text
    if t == "string":
        parts: list[str] = []
        for child in node.children:
            ct = child.type
            if ct == '"':
                continue
            if ct == "string_content":
                parts.append(child.text.decode(errors="replace"))
            else:
                # An expansion / command_substitution inside the string → dynamic.
                return None
        return "".join(parts)
    if t == "concatenation":
        out: list[str] = []
        for child in node.children:
            piece = _node_literal(child)
            if piece is None:
                return None
            out.append(piece)
        return "".join(out)
    # command_substitution, simple_expansion, expansion, number, etc. → dynamic
    # or non-literal; treat as unknown.
    return None


def _normalize_command_name(literal: str) -> str:
    """Strip a leading path so ``/bin/rm`` / ``./foo`` → the bare program name."""

    if "/" in literal:
        return literal.rsplit("/", 1)[-1]
    return literal


def _command_name_node(command: Any) -> Any | None:
    """The ``command_name`` child of a ``command`` node, or ``None``."""

    for child in command.children:
        if child.type == "command_name":
            return child
    return None


def _command_args(command: Any) -> list[Any]:
    """The argument nodes of a ``command`` (everything after ``command_name``)."""

    args: list[Any] = []
    seen_name = False
    for child in command.children:
        if child.type == "command_name":
            seen_name = True
            continue
        if not seen_name:
            # Leading ``variable_assignment`` (e.g. ``A=1 rm``) — skip.
            continue
        if child.type in ("file_redirect", "heredoc_redirect", "herestring_redirect"):
            continue
        args.append(child)
    return args


def _has_leading_assignment(command: Any) -> bool:
    """Whether a ``command`` node carries a ``VAR=value`` prefix before its name.

    A leading assignment can inject an environment variable the command then
    reads as code — ``LD_PRELOAD=./x.so cat`` preloads a library,
    ``GIT_SSH_COMMAND=./evil.sh git …`` / ``GIT_DIR=cache git status`` /
    ``GIT_CONFIG_GLOBAL=…`` point git at an attacker's files, ``PATH=.:$PATH ls``
    shadows a binary. #657 decision 3: ANY such prefix forces at least ASK, for
    EVERY command. DENY still wins (``a=1 rm`` stays DENY) because the caller
    takes ``max`` with the name's own verdict.
    """

    for child in command.children:
        if child.type == "command_name":
            return False
        if child.type == "variable_assignment":
            return True
    return False


# --- Static shell words (#657 review round 1) ----------------------------------
#
# ``_node_literal`` keeps every backslash the shell removes and cannot tell a
# quoted ``*`` from a glob. That is enough for a command NAME (an unresolved
# name is unknown, i.e. ASK), but not for the scans over ARGUMENTS that look for
# a dangerous option: Codex and verify got ``git diff --out\put=``,
# ``rg --p\re=`` and ``find . -de\lete`` past them, and a glob such as ``*``
# can expand into a planted file named ``--output=x``. An argument-sensitive
# command reads :func:`_static_word` instead, and asks on anything it returns
# ``None`` for.

_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class _Word:
    """One argument's value after the shell's quote and backslash removal.

    ``literal[i]`` says whether ``text[i]`` was quoted or escaped, which is what
    tells a ``*`` the shell expands from one it passes through.
    """

    text: str
    literal: tuple[bool, ...]

    @property
    def glob(self) -> bool:
        """Whether the shell may expand this word into other words."""

        return any(
            ch in _GLOB_CHARS and not lit for ch, lit in zip(self.text, self.literal, strict=True)
        )

    @property
    def may_be_option(self) -> bool:
        """Whether a tool could read this word as an option once it is expanded."""

        if self.text.startswith("-"):
            return True
        return bool(self.text) and self.text[0] in _GLOB_CHARS and not self.literal[0]

    def could_be(self, name: str) -> bool:
        """Whether this word, globbed against a planted file name, could be ``name``."""

        pattern = "".join(
            f"[{ch}]" if lit and ch in _GLOB_CHARS else ch
            for ch, lit in zip(self.text, self.literal, strict=True)
        )
        return fnmatch.fnmatchcase(name, pattern)


def _unescape(text: str, out: list[tuple[str, bool]], *, quoted: bool) -> None:
    """Append ``text`` with the shell's backslash removal applied.

    Unquoted, a backslash quotes the next character and ``\\<newline>`` (a line
    continuation) disappears. Inside double quotes only ``$``, `````, ``"``,
    ``\\`` and a newline are special after a backslash; any other backslash is
    kept.
    """

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and (not quoted or text[i + 1] in '$`"\\\n'):
            if text[i + 1] != "\n":
                out.append((text[i + 1], True))
            i += 2
            continue
        out.append((ch, quoted))
        i += 1


def _word_chars(node: Any, out: list[tuple[str, bool]]) -> bool:
    """Append ``node``'s characters after quote removal; ``False`` if it is dynamic."""

    t = node.type
    if t in ("word", "number"):
        _unescape(node.text.decode(errors="replace"), out, quoted=False)
        return True
    if t == "raw_string":
        out.extend((ch, True) for ch in node.text.decode(errors="replace")[1:-1])
        return True
    if t == "string":
        for child in node.children:
            if child.type == '"':
                continue
            if child.type != "string_content":
                return False  # an expansion inside the quotes
            _unescape(child.text.decode(errors="replace"), out, quoted=True)
        return True
    if t == "concatenation":
        return all(_word_chars(child, out) for child in node.children)
    # ``$var``, ``$(…)``, backticks, ``$'…'``, ``$"…"``: unknown until runtime.
    return False


def _has_brace_expansion(chars: list[tuple[str, bool]]) -> bool:
    """Whether an unquoted ``{a,b}`` / ``{1..3}`` turns this word into several.

    ``HEAD@{1}`` has no comma and no ``..``, so it stays one word.
    """

    bare = [(i, ch) for i, (ch, lit) in enumerate(chars) if not lit]
    opens = [i for i, ch in bare if ch == "{"]
    closes = [i for i, ch in bare if ch == "}"]
    if not opens or not closes or closes[-1] < opens[0]:
        return False
    inner = [(i, ch) for i, ch in bare if opens[0] < i < closes[-1]]
    if any(ch == "," for _, ch in inner):
        return True
    return any(
        a == b == "." and j == i + 1 for (i, a), (j, b) in zip(inner, inner[1:], strict=False)
    )


def _static_word(node: Any) -> _Word | None:
    """The shell's value for one argument node, or ``None`` when it is not static.

    Not static: any expansion or substitution, ``$'…'`` / ``$"…"``, and a brace
    expansion (``{--output=x,a}`` is two words). A glob is static TEXT whose
    expansion the caller must judge — see :attr:`_Word.glob`.
    """

    chars: list[tuple[str, bool]] = []
    if not _word_chars(node, chars) or _has_brace_expansion(chars):
        return None
    return _Word("".join(ch for ch, _ in chars), tuple(lit for _, lit in chars))


def _static_args(command: Any) -> list[_Word] | None:
    """Every argument's :func:`_static_word`, or ``None`` if any one is not static."""

    words: list[_Word] = []
    for arg in _command_args(command):
        word = _static_word(arg)
        if word is None:
            return None
        words.append(word)
    return words


def _has_continuation_join(command: Any) -> bool:
    """Whether a line continuation glues two of the command's words into one.

    tree-sitter-bash ends a word at ``\\<newline>`` and starts the next one after
    it, but the shell removes the pair first: ``--out\\<newline>put=x`` is ONE
    argument, ``--output=x``, and ``l\\<newline>s`` runs ``ls``. Reading such a
    command word by word would judge the wrong words, so it asks instead.
    """

    text = command.text
    base = command.start_byte
    kids = command.children
    for left, right in zip(kids, kids[1:], strict=False):
        gap = text[left.end_byte - base : right.start_byte - base]
        if gap and not gap.replace(b"\\\n", b""):
            return True
    return False


# --- What the shell evaluates while it expands (#657 decision 3, review round 2) --
#
# The bash tool's bare-repository guard is a set of EXPORTED variables
# (``GIT_CONFIG_COUNT`` / ``_KEY_<n>`` / ``_VALUE_<n>``), and git reads more of
# them for its config and repository (``GIT_DIR``, ``GIT_CONFIG_GLOBAL``, …).
# A shell assigns to a variable not only in a statement (``_ASSIGNMENT_STATEMENTS``)
# but while it EXPANDS a word, and round 1 reached only some of those spellings
# with a regex over the arithmetic / parameter / test contexts tree-sitter
# labels (B1–B3). This pass closes the class:
#
# - an ASSIGNMENT OPERATOR (``=``, ``+=``, ``++``, …) in an arithmetic context
#   (``$((X=0))``, ``((X--))``, ``$[X=0]``), in a substring offset
#   (``${x:X=0}``), in an array subscript (``${a[X=0]}``, zsh ``$path[X=0]`` /
#   ``$#path[X=0]`` — tree-sitter splits these into an expansion and a ``[``
#   word, B1), or as a ``test`` / ``[`` / ``[[`` operand git evaluates
#   (``test -v 'a[X=0]'``, ``test -t X=0``, ``[[ 1 -eq X=0 ]]``);
# - ``${X:=v}`` / ``${X=v}`` (assign-default), ``${!x}`` indirection and the
#   ``${x@P}`` transforms, which re-evaluate a model-controlled value;
# - a DYNAMIC word (``$x``, ``$(…)``, a file the model wrote) in any of those
#   arithmetic / name positions, because the shell evaluates its VALUE as an
#   expression and that value can carry an assignment (measured: ``x='V=5';
#   echo $((x))`` and ``[[ "$(cat f)" -ne 0 ]]`` assign in bash/ksh/zsh);
# - ``{X}>file`` / ``{X}<file``, which store a descriptor number in ``X``;
# - any ``GIT_*`` name anywhere in the source (below).
#
# A bare numeric or name operand that the model did NOT set in the command
# (``$(( i < 10 ))``, ``${a[i]}``) is left ALLOW: reaching it needs a prior
# assignment statement, which already asks. Measured per rule in zsh 5.9, bash
# 3.2 and 5.2, ksh and dash (``.omc/specs/657-fix-r2-evidence/``).

# Every ``GIT_*`` name, wherever the command spells it. git reads dozens for
# config, repository discovery and programs it runs (``GIT_DIR``,
# ``GIT_CONFIG_*``, ``GIT_SSH*``, ``GIT_EXTERNAL_DIFF``, …), so — like decision
# 1's refusal of a safe-key list — the whole family asks rather than a list that
# would drift. ``HOME`` / ``XDG_CONFIG_HOME`` are NOT here (git's global config
# lives under them, but a mention is far too common to ask on); they are caught
# only in the assignment positions above.
_GUARD_NAME = re.compile(r"(?<![A-Za-z0-9_])GIT_[A-Za-z0-9_]")
# An assignment the shell performs while evaluating arithmetic: ``=`` (but not
# ``==`` / ``<=`` / ``>=`` / ``!=``), ``++`` / ``--``, and the compound
# ``op=`` forms. Round 1's expression, applied here only to arithmetic / index
# text, never to a parameter body (so ``${x%%=*}`` is not read as an
# assignment).
_ASSIGNMENT_OPERATOR = re.compile(r"(?<![=!<>])=(?!=)|\+\+|--|[-+*/%&|^]=|<<=|>>=")
# A safe array / substring index: ``@``, ``*``, a number (or a ``n,m`` range),
# or a single bare identifier (``${a[i]}`` reads, it does not assign). Anything
# with an operator, a ``$`` or a ``=`` falls through to ASK.
_SAFE_INDEX = re.compile(r"\s*(?:[@*]|[A-Za-z_][A-Za-z0-9_]*|[-+]?[0-9]+(?:\s*,\s*[-+]?[0-9]+)?)\s*")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# zsh subscripts a plain ``$name[…]``, ``$#name[…]`` (and ``$+``/``$=``/``$~``/
# ``$^`` forms); tree-sitter splits these into an expansion and a ``[`` word.
_ZSH_SUBSCRIPT = re.compile(rb"\$[#+=~^]*(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]|[@*#?$!_-])\[")
_ARITHMETIC_TEST_OPERATORS = frozenset({"-eq", "-ne", "-lt", "-le", "-gt", "-ge"})
_NAME_TEST_OPERATORS = frozenset({"-v", "-R"})
# ``test`` / ``[`` operators whose operand is only a string or a path. ``-t``
# (zsh evaluates its descriptor) and the name / arithmetic ones are not here.
_INERT_UNARY_TESTS = frozenset("-" + c for c in "abcdefghknoprsuwxzGLNOS")
_INERT_BINARY_TESTS = frozenset({"=", "==", "!=", "<", ">", "-nt", "-ot", "-ef"})
_REDIRECT_NODES = frozenset({"file_redirect", "heredoc_redirect", "herestring_redirect"})
# Source ranges the shell never expands.
_LITERAL_NODES = frozenset({"raw_string", "ansi_c_string", "comment"})
_EXPANSION_NODES = frozenset(
    {"simple_expansion", "expansion", "command_substitution", "arithmetic_expansion"}
)


def _guard_name_pattern() -> re.Pattern[str]:
    """``_GUARD_NAME``, case-folded where environment names fold (Windows)."""

    if sys.platform == "win32":
        return re.compile(_GUARD_NAME.pattern, re.IGNORECASE)
    return _GUARD_NAME


_NUMERIC_SPECIALS = frozenset({"#", "?", "$", "!"})


def _numeric_expansion(node: Any) -> bool:
    """Whether an expansion always yields a number: ``$#``/``$?``/``$$``/``$!``, ``${#x}``.

    These are safe in an arithmetic position because their value cannot carry an
    assignment, unlike ``$x`` / ``$(…)`` whose value the shell re-evaluates.
    """

    if node.type == "simple_expansion":
        kids = [child for child in node.children if child.is_named]
        return (
            len(kids) == 1
            and kids[0].type == "special_variable_name"
            and kids[0].text.decode(errors="replace") in _NUMERIC_SPECIALS
        )
    if node.type == "expansion":
        return len(node.children) >= 2 and node.children[1].type == "#"  # ``${#x}``
    return False


def _dynamic_nonnumeric(node: Any) -> bool:
    """Whether the subtree holds an expansion / substitution whose value is not a number."""

    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in ("simple_expansion", "expansion", "command_substitution"):
            if not _numeric_expansion(current):
                return True
            continue
        if current.type == "arithmetic_expansion":
            continue  # a nested ``$((…))`` is judged on its own by the recursion
        stack.extend(current.children)
    return False


def _arith_danger(node: Any) -> bool:
    """Whether an arithmetic context assigns or evaluates a model-controlled value.

    An assignment operator in its text, or a dynamic word (``$x`` / ``$(…)``)
    whose VALUE the shell evaluates as an expression. A bare number or name, and
    ``$#`` / ``${#x}``, are inert.
    """

    return _dynamic_nonnumeric(node) or bool(
        _ASSIGNMENT_OPERATOR.search(node.text.decode(errors="replace"))
    )


def _expr_danger(node: Any) -> bool:
    """Whether one ``test`` / ``[[`` arithmetic operand assigns or is a dynamic non-number."""

    if node.type in ("simple_expansion", "expansion", "command_substitution"):
        return not _numeric_expansion(node)
    return _arith_danger(node)


def _index_danger(text: str) -> bool:
    """Whether a subscript / substring index assigns or is a dynamic word."""

    return "$" in text or "`" in text or not _SAFE_INDEX.fullmatch(text)


def _safe_subscript(node: Any) -> bool:
    """Whether a ``subscript`` node's index neither assigns nor is dynamic."""

    inside = node.children[2:]
    text = b"".join(child.text for child in inside if child.type != "]").decode(errors="replace")
    return not _index_danger(text)


def _bracket_content(source: bytes, start: int) -> bytes | None:
    """The text between ``source[start]`` (a ``[``) and its matching ``]``."""

    depth = 0
    for i in range(start, len(source)):
        if source[i : i + 1] == b"[":
            depth += 1
        elif source[i : i + 1] == b"]":
            depth -= 1
            if depth == 0:
                return source[start + 1 : i]
    return None


def _ranges(
    node: Any, types: frozenset[str], *, descend: bool = False
) -> list[tuple[int, int]]:
    """Byte ranges of the nodes under ``node`` whose type is in ``types``.

    Outermost only, unless ``descend`` also collects the ones nested in them.
    """

    found: list[tuple[int, int]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in types:
            found.append((current.start_byte, current.end_byte))
            if not descend:
                continue
        stack.extend(current.children)
    return found


def _quoted_heredoc_bodies(root: Any) -> list[tuple[int, int]]:
    """Byte ranges of here-document bodies whose delimiter is quoted (not expanded)."""

    found: list[tuple[int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "heredoc_redirect" and not _heredoc_expands(node):
            found.extend(_ranges(node, frozenset({"heredoc_body"})))
        stack.extend(node.children)
    return found


def _heredoc_expands(redirect: Any) -> bool:
    """Whether a here-document's body is expanded: its delimiter has no quote or ``\\``."""

    for child in redirect.children:
        if child.type == "heredoc_start":
            return not any(ch in child.text for ch in b"'\"\\")
    return True


def _test_arguments_evaluate(command: Any) -> bool:
    """Whether a ``test`` / ``[`` command could evaluate an operand as a name or arithmetic.

    ``test -v 'a[X=0]'`` evaluates the subscript in bash 5 and ksh, zsh
    evaluates ``test -t X=0``, ksh ``test 1 -eq X=0``. A word the shell splits
    (``test -v $(cat f)``) can become any operator, so it asks; a quoted
    expansion is allowed only where the POSIX argument-count rules make it an
    operand of a string or file test (``test -f "$x"``, ``[ "$a" = b ]``).
    """

    args = _command_args(command)
    words: list[str | None] = []
    for arg in args:
        word = _static_word(arg)
        if word is None and arg.type != "string":
            return True  # an unquoted expansion is split into any words
        words.append(None if word is None else word.text)
    if words and words[-1] == "]":
        words.pop()
    if None in words:
        n = len(words)
        shapes = (
            n == 1,
            n == 2 and words[0] in _INERT_UNARY_TESTS | {"!"},
            n == 3 and words[1] in _INERT_BINARY_TESTS,
            n == 3 and words[0] == "!" and words[1] in _INERT_UNARY_TESTS,
        )
        if not any(shapes):
            return True
    for i, word in enumerate(words):
        after = words[i + 1] if i + 1 < len(words) else None
        before = words[i - 1] if i else None
        if word in _NAME_TEST_OPERATORS and after is not None and not _IDENTIFIER.fullmatch(after):
            return True
        if word == "-t" and after is not None and _index_danger(after):
            return True
        if word in _ARITHMETIC_TEST_OPERATORS and any(
            side is not None and _index_danger(side) for side in (before, after)
        ):
            return True
    return False


def _node_evaluates(node: Any) -> bool:
    """Whether ``node``'s subtree holds an expansion-time assignment or evaluation."""

    t = node.type
    if t in _LITERAL_NODES:
        return False
    arithmetic = t == "arithmetic_expansion" or (
        t == "compound_statement" and node.children and node.children[0].type == "(("
    )
    if arithmetic and _arith_danger(node):
        return True
    if t == "subscript" and not _safe_subscript(node):
        return True
    if t == "expansion":
        tokens = [child.type for child in node.children if not child.is_named]
        # ``${!x}`` indirection, ``${x@P}`` and the other transforms, ``${x=v}``
        # / ``${x:=v}``.
        if any(tok in ("!", "@", "=", ":=") for tok in tokens):
            return True
        kids = node.children
        for left, right in zip(kids, kids[1:], strict=False):
            if left.type == ":" and right.is_named and _index_danger(
                right.text.decode(errors="replace")
            ):
                return True  # a substring offset or length is arithmetic
    if t == "test_command" and _test_command_evaluates(node):
        return True
    if t == "command":
        name_node = _command_name_node(node)
        inner = name_node.children[0] if name_node is not None and name_node.children else None
        literal = _node_literal(inner) if inner is not None else None
        if literal in ("test", "[") and _test_arguments_evaluate(node):
            return True
    return any(_node_evaluates(child) for child in node.children)


def _test_command_evaluates(node: Any) -> bool:
    """``[[ … ]]``: an arithmetic / ``-t`` operand that assigns or is dynamic, a ``-v`` non-name."""

    if node.type in ("binary_expression", "unary_expression"):
        ops = [child for child in node.children if child.type == "test_operator"]
        operands = [c for c in node.children if c.is_named and c.type != "test_operator"]
        op = ops[0].text.decode(errors="replace") if ops else ""
        if (op in _ARITHMETIC_TEST_OPERATORS or op == "-t") and any(
            _expr_danger(child) for child in operands
        ):
            return True
        if op in _NAME_TEST_OPERATORS:
            words = [_static_word(child) for child in operands]
            if not all(w is not None and _IDENTIFIER.fullmatch(w.text) for w in words):
                return True
    return any(_test_command_evaluates(child) for child in node.children)


def _evaluates_while_expanding(root: Any, source: bytes) -> bool:
    """Whether running ``source`` could assign, or evaluate a word as a name or as arithmetic.

    ASK — never ALLOW — when it does, because that is how a command switches
    the guard off without an assignment statement (#657 decision 3).
    """

    if _guard_name_pattern().search(source.decode(errors="replace")):
        return True
    literal = _ranges(root, _LITERAL_NODES) + _quoted_heredoc_bodies(root)
    for match in _ZSH_SUBSCRIPT.finditer(source):
        if any(lo <= match.start() < hi for lo, hi in literal):
            continue
        content = _bracket_content(source, match.end() - 1)
        if content is None or _index_danger(content.decode(errors="replace")):
            return True
    # ``${x}[…]`` is a glob in zsh (measured), but nothing is lost by asking.
    for _lo, hi in _ranges(root, frozenset({"expansion"}), descend=True):
        if source[hi : hi + 1] == b"[":
            content = _bracket_content(source, hi)
            if content is None or _index_danger(content.decode(errors="replace")):
                return True
    # ``{name}>file`` / ``{name}<file`` store a descriptor number in ``name``
    # (bash 4.1+, zsh, ksh — measured for a builtin, which runs in the shell).
    for lo, _ in _ranges(root, _REDIRECT_NODES, descend=True):
        if lo and source[lo - 1 : lo] == b"}":
            return True
    return _node_evaluates(root)


def _classify_simple_command(command: Any, dialect: Dialect) -> Verdict:
    """Classify a single ``command`` node (allowlist / denylist / git rules).

    ``dialect`` reaches exactly ONE decision point — the ALLOW tier's
    :func:`_classify_dialect_read_only` (#204). Every other branch here is a
    fact about a command NAME, which no dialect changes, EXCEPT the #657
    argument reading below: a read-only NAME whose ARGUMENTS write or execute
    (``env <cmd>``, ``rg --pre``, ``uniq in out``, ``tree -o``, ``git -c``) is
    downgraded to ASK, mirroring the ``sort -o`` / ``find -exec`` precedent.
    """

    name_node = _command_name_node(command)
    if name_node is None:
        return Verdict.ASK
    # ``command_name`` wraps the actual literal node (word / concatenation / …).
    inner = name_node.children[0] if name_node.children else name_node
    literal = _node_literal(inner)
    if literal is None:
        # Dynamic command name ($(…) / $VAR) — can't reason about it.
        return Verdict.ASK
    name = _normalize_command_name(literal)
    if not name:
        return Verdict.ASK
    verdict = _classify_by_name(command, name, dialect)
    # #657 decision 3: a ``VAR=value`` assignment prefix forces at least ASK, and
    # so does a word the parser split where the shell does not.
    if _has_leading_assignment(command) or _has_continuation_join(command):
        return max(verdict, Verdict.ASK)
    return verdict


def _classify_by_name(command: Any, name: str, dialect: Dialect) -> Verdict:
    """The name-keyed verdict, before the assignment-prefix adjustment (#657)."""

    if name in _DENY_COMMANDS:
        return Verdict.DENY
    # ``mkfs.ext4`` / ``mkfs.xfs`` / … are the real filesystem-format programs —
    # match the ``mkfs.`` family, not just the bare ``mkfs`` alias.
    if name.startswith("mkfs.") or name.startswith("mke2fs"):
        return Verdict.DENY
    if name == "git":
        return _classify_git(command)
    if name in ("find", "fd"):
        return _classify_find(command, name)
    if name in _RECURSIVE_MUTATORS:
        return _classify_recursive_mutator(command)
    if name in _ALWAYS_ASK_COMMANDS:
        return Verdict.ASK
    if name in _READ_ONLY:
        if name in _READ_ONLY_SWITCHES:
            return _classify_dialect_read_only(command, name, dialect)
        if name in _ARG_SENSITIVE_READ_ONLY:
            return _classify_arg_sensitive_read_only(command, name, dialect)
        return Verdict.ALLOW
    # Unknown command → ASK (never silent-allow).
    return Verdict.ASK


# Read-only NAMES in ``_READ_ONLY`` whose ARGUMENTS can turn them into a write
# or an exec — the #657 sweep. Each is read by :func:`_classify_arg_sensitive_read_only`
# rather than auto-allowed on the name alone. (``sort``/``date``/``hostname``
# are the dialect-sensitive case and stay in ``_READ_ONLY_SWITCHES`` instead.)
#
# Review round 1 replaced the exact-token scans with the tool's own option
# grammar: ``--`` ends the options, ``-`` is an operand, a value-taking option
# consumes its value (attached or the next word) so the value is never read as
# an option, a short cluster is expanded, and where the tool accepts an
# abbreviated long option any prefix of a dangerous one counts as that option.
_ARG_SENSITIVE_READ_ONLY = frozenset({"env", "rg", "uniq", "tree", "yq", "file"})


def _abbreviates(key: str, names: tuple[str, ...]) -> bool:
    """Whether a getopt_long / git parse-options tool could read ``key`` as one of ``names``.

    That is the name itself or any prefix of it from ``--x`` up. Used only to
    REFUSE, so a prefix the tool would reject as ambiguous still asks.
    """

    return key.startswith("--") and len(key) > 2 and any(n.startswith(key) for n in names)


def _getopt(
    words: list[_Word],
    *,
    short_values: str = "",
    long_values: frozenset[str] = frozenset(),
    permute: bool = True,
    stop_at_dashdash: bool = True,
    long_abbrev: bool = False,
) -> tuple[list[str], list[_Word]] | None:
    """Split ``words`` the way a getopt-style parser does: (options, operands).

    Options come back as written (``-x`` per short-cluster letter, ``--key``
    without its ``=value``). A value is consumed only for a ``long_values`` name
    (an abbreviated prefix of one too when ``long_abbrev``, which is how
    getopt_long reads ``file``/``yq``) or a ``short_values`` letter: guessing a
    value slot could hide the next word, while missing one only reads a value as
    an option (a prompt, never a pass). ``permute=False`` is BSD getopt, where
    the first operand ends the options.

    ``stop_at_dashdash`` is ``False`` for the write/exec tools whose ``--`` a
    value-taking option can swallow (#657 review round 2, B4): there a ``--`` is
    skipped and option parsing continues, so a dangerous option after it is not
    hidden — at the cost of a path literally named like an option asking.

    ``None`` when a glob could expand into an option or into a value slot.
    """

    options: list[str] = []
    operands: list[_Word] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        tok = word.text
        if operands and not permute:
            operands.append(word)
            continue
        if tok == "--":
            if stop_at_dashdash:
                operands.extend(words[i:])
                break
            continue
        if word.glob and word.may_be_option:
            return None
        if tok.startswith("--"):
            key, eq, _ = tok.partition("=")
            options.append(key)
            takes_value = key in long_values or (long_abbrev and _abbreviates(key, tuple(long_values)))
            if not eq and takes_value and i < len(words):
                if words[i].glob:
                    return None
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            for j, ch in enumerate(tok[1:], 1):
                options.append(f"-{ch}")
                if ch in short_values:
                    if j + 1 == len(tok) and i < len(words):
                        if words[i].glob:
                            return None
                        i += 1
                    break
            continue
        operands.append(word)
    return options, operands


# ripgrep 15 reads its flags with lexopt: no abbreviation (``--pr=``,
# ``--hostname-b=`` are refused, measured), and a value flag takes ``=v`` or
# the next word. ``--pre`` and ``--hostname-bin`` run a program; ``--pre-glob``
# only scopes ``--pre`` but stays ASK as before.
_RG_SHORT_VALUES = "efEmjgdtTABCMr"
_RG_LONG_VALUES = frozenset(
    {
        "--regexp", "--file", "--encoding", "--max-count", "--threads", "--glob",
        "--iglob", "--max-depth", "--type", "--type-not", "--after-context",
        "--before-context", "--context", "--max-columns", "--replace", "--pre",
        "--pre-glob", "--hostname-bin", "--dfa-size-limit", "--engine",
        "--regex-size-limit", "--ignore-file", "--max-filesize", "--type-add",
        "--type-clear", "--color", "--colors", "--context-separator",
        "--field-context-separator", "--field-match-separator",
        "--hyperlink-format", "--path-separator", "--sort", "--sortr", "--generate",
    }
)  # fmt: skip
_RG_EXEC = frozenset({"--pre", "--pre-glob", "--hostname-bin"})

# file 5.41 (getopt_long): ``-C``/``--compile`` writes ``<magic>.mgc``;
# ``-f``/``--files-from`` turns one argument into an unbounded set of reads
# (the ``--files0-from`` / ``findstr /f:`` doctrine). ``-M`` is macOS-only.
_FILE_SHORT_VALUES = "efFmMP"
_FILE_LONG_VALUES = frozenset(
    {"--magic-file", "--exclude", "--exclude-quiet", "--separator", "--parameter", "--files-from"}
)

# mikefarah yq (cobra/pflag): ``-i``/``--inplace`` edits in place, also as
# ``-i=true`` or inside a cluster (``-Pi``); ``-s``/``--split-exp`` writes one
# file per result. kislyuk yq (argparse) spells it ``--in-place``.
_YQ_SHORT_VALUES = "Iops"
_YQ_LONG_VALUES = frozenset(
    {
        "--indent", "--output-format", "--input-format", "--expression",
        "--from-file", "--front-matter", "--split-exp", "--split-exp-file",
    }
)  # fmt: skip
_YQ_WRITES = ("--inplace", "--in-place", "--split-exp", "--split-exp-file")

# tree 2.x: ``-o`` writes the listing to a file and ``-R`` (with ``-H``) writes
# ``00Tree.html`` into every directory. Each value letter in a cluster takes the
# NEXT word (``tree -Io out x .`` writes ``x``, measured), so an exact value
# option consumes one word and any cluster holding ``o`` or ``R`` asks.
_TREE_VALUE_OPTIONS = frozenset({"-L", "-P", "-I", "-H", "-T", "-o"})

# GNU uniq permutes; BSD/macOS uniq stops at the first operand, so ``uniq in -c``
# writes a file named ``-c`` there (measured). Both readings must find at most
# one operand.
_UNIQ_LONG_VALUES = frozenset({"--skip-fields", "--skip-chars", "--check-chars"})

# env (GNU and BSD): options with a value, and ``-S``/``--split-string``, whose
# string becomes the command.
_ENV_SHORT_VALUES = "uCSaPLU"
_ENV_LONG_VALUES = frozenset({"--unset", "--chdir", "--split-string", "--argv0"})


def _classify_arg_sensitive_read_only(command: Any, name: str, dialect: Dialect) -> Verdict:
    """ALLOW a read-only name only when its arguments neither write nor execute.

    A non-static argument (an expansion, a substitution, a brace expansion) can
    be anything, so it ASKs — the same fail-safe the rest of the module uses.
    """

    if name == "env":
        return _classify_env(command, dialect)
    words = _static_args(command)
    if words is None:
        return Verdict.ASK

    if name == "tree":
        return _classify_tree(words)

    if name == "uniq":
        gnu = _getopt(words, short_values="fsw", long_values=_UNIQ_LONG_VALUES)
        bsd = _getopt(words, short_values="fs", long_values=_UNIQ_LONG_VALUES, permute=False)
        if gnu is None or bsd is None:
            return Verdict.ASK
        # ``uniq [INPUT [OUTPUT]]``: a second operand is the OUTPUT file, and a
        # globbed operand may be two.
        for _, operands in (gnu, bsd):
            if len(operands) > 1 or any(word.glob for word in operands):
                return Verdict.ASK
        return Verdict.ALLOW

    # B4 (#657 review round 2): ``--`` does NOT end the dangerous-option scan
    # for these three — a value-taking option can swallow it (``file --mag --
    # -C`` writes), and after ``--`` the tool still reads the next word as an
    # option. ``stop_at_dashdash=False`` keeps scanning; a path named like an
    # option asking is the accepted cost.
    if name == "rg":
        parsed = _getopt(
            words, short_values=_RG_SHORT_VALUES, long_values=_RG_LONG_VALUES,
            stop_at_dashdash=False,
        )  # fmt: skip
        if parsed is None or any(opt in _RG_EXEC for opt in parsed[0]):
            return Verdict.ASK
        return Verdict.ALLOW

    if name == "file":
        parsed = _getopt(
            words, short_values=_FILE_SHORT_VALUES, long_values=_FILE_LONG_VALUES,
            stop_at_dashdash=False, long_abbrev=True,
        )  # fmt: skip
        if parsed is None or any(
            opt in ("-C", "-f") or _abbreviates(opt, ("--compile", "--files-from"))
            for opt in parsed[0]
        ):
            return Verdict.ASK
        return Verdict.ALLOW

    if name == "yq":
        parsed = _getopt(
            words, short_values=_YQ_SHORT_VALUES, long_values=_YQ_LONG_VALUES,
            stop_at_dashdash=False, long_abbrev=True,
        )  # fmt: skip
        if parsed is None or any(
            opt in ("-i", "-s") or _abbreviates(opt, _YQ_WRITES) for opt in parsed[0]
        ):
            return Verdict.ASK
        return Verdict.ALLOW

    return Verdict.ALLOW


def _classify_tree(words: list[_Word]) -> Verdict:
    """``tree`` → ASK on ``-o`` / ``-R`` anywhere in a cluster (see ``_TREE_VALUE_OPTIONS``).

    ``-I`` swallows a ``--`` (tree 2.3.2 ends options at a bare one), so
    ``tree -aI -- -o V .`` writes ``V`` (measured, B4): the scan runs past it.
    """

    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        tok = word.text
        if tok == "--":
            continue
        if word.glob and word.may_be_option:
            return Verdict.ASK
        if tok in _TREE_VALUE_OPTIONS:
            if tok == "-o":
                return Verdict.ASK
            if i < len(words):
                if words[i].glob:
                    return Verdict.ASK
                i += 1
            continue
        if tok.startswith("--"):
            if _abbreviates(tok.partition("=")[0], ("--output",)):
                return Verdict.ASK
            continue
        if tok.startswith("-") and ("o" in tok[1:] or "R" in tok[1:]):
            return Verdict.ASK
    return Verdict.ALLOW


def _classify_env(command: Any, dialect: Dialect) -> Verdict:
    """``env`` → ALLOW bare or ``-0``; otherwise ASK, or the command it runs if worse.

    ``env`` alone prints the environment, like ``printenv``. Any option or
    ``NAME=value`` changes the environment of a command (#657 decision 3), so
    it asks; and the command after them is classified from its own source text,
    so ``env rm -rf /`` is DENY like ``rm -rf /``. ``-S`` hands a string to the
    shell-like splitter and is not read further.
    """

    args = _command_args(command)
    words = [_static_word(arg) for arg in args]
    if all(
        w is not None and not w.glob and (w.text == "-0" or _abbreviates(w.text, ("--null",)))
        for w in words
    ):
        return Verdict.ALLOW
    i = 0
    while i < len(words):
        word = words[i]
        if word is None or word.glob:
            return Verdict.ASK
        tok = word.text
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            key, eq, _ = tok.partition("=")
            if _abbreviates(key, ("--split-string",)):
                return Verdict.ASK
            # GNU env accepts an abbreviated long option, so ``env --ch dir rm``
            # is ``--chdir dir`` and the command after it is read — ``env --ch
            # dir rm -rf /`` is DENY like ``env -C dir rm -rf /`` (nit).
            takes_value = key in _ENV_LONG_VALUES or _abbreviates(key, tuple(_ENV_LONG_VALUES))
            i += 2 if takes_value and not eq else 1
            continue
        if tok.startswith("-"):  # also a lone ``-``, the obsolete ``-i``
            i += 1
            for j, ch in enumerate(tok[1:], 1):
                if ch == "S":
                    return Verdict.ASK
                if ch in _ENV_SHORT_VALUES:
                    i += 1 if j + 1 == len(tok) else 0
                    break
            continue
        break
    while i < len(words):
        word = words[i]
        if word is None or word.glob:
            return Verdict.ASK
        if "=" not in word.text:
            break
        i += 1  # NAME=value
    if i >= len(args):
        return Verdict.ASK
    inner = command.text[args[i].start_byte - command.start_byte :].decode(errors="replace")
    return max(Verdict.ASK, classify(inner, dialect=dialect))


# git GLOBAL options (before the subcommand) that take a value or change which
# repository / config the command reads — ALL ASK (#657 decisions 1 and 2).
# ``-c``/``--config-env`` set arbitrary config (``core.fsmonitor``,
# ``core.sshCommand``, …); ``-C``/``--git-dir``/``--work-tree``/``GIT_DIR`` point
# git at a planted repository. Matched on the ``=``-stripped key, so both the
# ``--git-dir X`` and ``--git-dir=X`` spellings are caught.
_GIT_GLOBAL_ASK_FLAGS = frozenset(
    {
        "-c",
        "-C",
        "--config-env",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--exec-path",
        "--super-prefix",
        "--attr-source",
    }
)

# git GLOBAL options that are value-less, read-only booleans — safe to pass
# before a read-only subcommand. An ALLOWLIST, not a denylist: any OTHER
# ``-``-leading global token (an unknown or value-bearing one) falls through to
# ASK, so a future value-bearing global flag cannot be mistaken for a boolean
# and let its value be read as the subcommand. ``--bare`` is NOT here (review
# round 1, V1): it names the working folder as the repository explicitly, so
# ``safe.bareRepository=explicit`` lets a planted ``config`` there run.
_GIT_GLOBAL_SAFE_FLAGS = frozenset(
    {
        "-p",
        "-P",
        "--paginate",
        "--no-pager",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
        "--no-lazy-fetch",
        "--no-advice",
        "--version",
        "--help",
        "--html-path",
        "--man-path",
        "--info-path",
        "--list-cmds",
    }
)


@dataclass(frozen=True)
class _GitOpt:
    """One entry of a git parse-options table, as the read-shape allowlist sees it.

    ``value``: ``""`` takes none; ``"required"`` takes ``=v`` or the next word;
    ``"optional"`` takes only an attached ``=v`` (``--color always`` makes
    ``always`` a branch name, measured); ``"lastarg"`` takes the next word when
    there is one (``--contains``, ``--merged``). ``marker``: a read action
    (``config --get``) or a flag that puts ``branch``/``tag`` in list mode.
    """

    safe: bool
    value: str = ""
    marker: bool = False


_OK = _GitOpt(safe=True)
_NO = _GitOpt(safe=False)
_OK_VALUE = _GitOpt(safe=True, value="required")
_OK_OPTARG = _GitOpt(safe=True, value="optional")
_MARK = _GitOpt(safe=True, marker=True)
_MARK_VALUE = _GitOpt(safe=True, value="required", marker=True)
_MARK_LASTARG = _GitOpt(safe=True, value="lastarg", marker=True)
_MARK_OPTARG = _GitOpt(safe=True, value="optional", marker=True)


def _negatable(names: str, spec: _GitOpt) -> dict[str, _GitOpt]:
    """``--[no-]<name>`` rows, as ``git <cmd> -h`` prints them."""

    rows: dict[str, _GitOpt] = {}
    for name in names.split():
        rows[f"--{name}"] = spec
        rows[f"--no-{name}"] = _OK if spec.safe else _NO
    return rows


# The tables below are each command's WHOLE option list from git 2.54's ``-h``,
# not just its safe part: an abbreviation resolves against all of them, the way
# git's parse-options does, so ``--fi`` is ambiguous (``--file``,
# ``--fixed-value``) rather than a silent ``--fixed-value``.
_GIT_CONFIG_LOCATION = {
    **_negatable("global system local worktree", _OK),
    **_negatable("file blob", _NO),
}
_GIT_CONFIG_DISPLAY = _negatable("null name-only show-origin show-scope show-names", _OK)
_GIT_CONFIG_TYPE = {
    **_negatable("type", _OK_VALUE),
    **dict.fromkeys(("--bool", "--int", "--bool-or-int", "--bool-or-str", "--path"), _OK),
    "--expiry-date": _OK,
}
_GIT_CONFIG_LIST_LONG = {
    **_GIT_CONFIG_LOCATION,
    **_GIT_CONFIG_DISPLAY,
    **_GIT_CONFIG_TYPE,
    **_negatable("includes", _OK),
}
_GIT_CONFIG_GET_LONG = {
    **_GIT_CONFIG_LIST_LONG,
    **_negatable("all regexp fixed-value", _OK),
    **_negatable("value url default", _OK_VALUE),
}
_GIT_CONFIG_LEGACY_LONG = {
    **_GIT_CONFIG_LIST_LONG,
    **_negatable("fixed-value", _OK),
    **_negatable("default", _OK_VALUE),
    **_negatable("comment", _NO),
    **dict.fromkeys(
        ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color", "--get-colorbool"),
        _MARK,
    ),
    "--list": _MARK,
    **dict.fromkeys(
        (
            "--replace-all",
            "--add",
            "--unset",
            "--unset-all",
            "--rename-section",
            "--remove-section",
            "--edit",
        ),
        _NO,
    ),  # fmt: skip
}
_GIT_CONFIG_LEGACY_SHORT = {"l": _MARK, "z": _OK, "t": _OK_VALUE, "f": _NO, "e": _NO}
_GIT_CONFIG_SUBCOMMAND_SHORT = {"z": _OK, "t": _OK_VALUE, "f": _NO}

_GIT_BRANCH_LONG = {
    **_negatable("verbose quiet omit-empty show-current ignore-case", _OK),
    **_negatable("color abbrev column", _OK_OPTARG),
    **_negatable("sort format", _OK_VALUE),
    **_negatable(
        "track set-upstream-to unset-upstream delete move copy create-reflog"
        " edit-description force recurse-submodules",
        _NO,
    ),
    "--list": _MARK,
    "--no-list": _OK,
    "--remotes": _OK,
    "--all": _OK,
    "--contains": _MARK_LASTARG,
    "--no-contains": _MARK_LASTARG,
    "--merged": _MARK_LASTARG,
    "--no-merged": _MARK_LASTARG,
    "--points-at": _MARK_VALUE,
    "--no-points-at": _OK,
}
_GIT_BRANCH_SHORT = {
    **dict.fromkeys("vqrai", _OK),
    "l": _MARK,
    **dict.fromkeys("tudDmMcCf", _NO),
}
_GIT_TAG_LONG = {
    **_negatable("omit-empty ignore-case", _OK),
    **_negatable("column color", _OK_OPTARG),
    **_negatable("sort format", _OK_VALUE),
    **_negatable("annotate file edit sign cleanup local-user force create-reflog trailer", _NO),
    "--list": _MARK,
    "--delete": _NO,
    "--verify": _NO,
    "--message": _NO,
    "--contains": _MARK_LASTARG,
    "--no-contains": _MARK_LASTARG,
    "--merged": _MARK_LASTARG,
    "--no-merged": _MARK_LASTARG,
    "--points-at": _MARK_VALUE,
    "--no-points-at": _OK,
}
_GIT_TAG_SHORT = {
    "i": _OK,
    "l": _MARK,
    "n": _MARK_OPTARG,
    **dict.fromkeys("dvamFesuf", _NO),
}
_GIT_REMOTE_LONG = _negatable("verbose", _OK)
_GIT_REMOTE_GET_URL_LONG = _negatable("push all", _OK)

# ``git reflog <sub>`` is a subcommand only as the FIRST word (``git reflog
# --all expire`` reads a ref named ``expire``, measured); these four write.
_GIT_REFLOG_WRITES = frozenset({"expire", "delete", "drop", "write"})

# Subcommands whose revision / diff options take their value as the next word,
# so ``git log --grep --output`` greps for ``--output``. Measured per
# subcommand on git 2.54: none of these wrote a file after any of the options
# below, while ``blame -n`` and ``shortlog -n`` / ``--committer`` are booleans
# there and did write — so those two get no value slots.
_GIT_LOG_FAMILY = frozenset({"log", "show", "whatchanged", "reflog", "rev-list", "diff"})
_GIT_LOG_VALUE_OPTIONS = frozenset(
    {
        "--grep", "--author", "--committer", "--grep-reflog", "-S", "-G", "-O",
        "--since", "--after", "--until", "--before", "-n", "--max-count", "--skip",
    }
)  # fmt: skip
# ``--output`` writes any path and ``--ext-diff`` runs the configured external
# diff. git rejects their abbreviations in revision mode but ``diff --no-index``
# accepts them (``--ext-dif``, Codex F03), so any prefix counts.
_GIT_WRITE_OR_EXEC_OPTIONS = ("--output", "--ext-diff")
# The read-only subcommands that ACCEPT ``--output`` / ``--ext-diff`` (measured
# on git 2.54: each wrote a file; the rest reject the option). Only here can a
# glob expand into a planted ``--output=…`` file, so ``git ls-files *.md`` is a
# read (should-fix) while ``git diff *.py`` still asks.
_GIT_OUTPUT_SUBCOMMANDS = frozenset(
    {"log", "show", "diff", "whatchanged", "blame", "shortlog", "reflog", "rev-list"}
)


def _classify_git(command: Any) -> Verdict:
    """``git`` → ALLOW only for a read-only subcommand with read-only arguments.

    The gate is now in three parts (#657):

    1. a ``VAR=value`` prefix (handled by the caller) forces ASK;
    2. the GLOBAL option phase ASKs on ``-c`` / ``--config-env`` (arbitrary
       config) and ``-C`` / ``--git-dir`` / ``--work-tree`` (a planted repo),
       and on any global option not in the value-less safe allowlist;
    3. the subcommand's OWN arguments are read, so a write / edit / transport /
       state-destroying form of an otherwise read-only subcommand ASKs.

    Every argument is read as a static shell word; a non-static one or a glob
    in the global phase asks.
    """

    args = _static_args(command)
    if args is None:
        return Verdict.ASK  # a dynamic argument — can't prove it read-only
    i = 0
    n = len(args)
    # Phase 2 — global options, up to the subcommand token.
    while i < n:
        word = args[i]
        if word.glob:
            return Verdict.ASK
        tok = word.text
        if not tok.startswith("-"):
            break  # the subcommand
        key = tok.split("=", 1)[0]
        if key in _GIT_GLOBAL_ASK_FLAGS:
            return Verdict.ASK
        if tok in _GIT_GLOBAL_SAFE_FLAGS or key in _GIT_GLOBAL_SAFE_FLAGS:
            i += 1
            continue
        # Unknown global option → ASK (it may consume the next token).
        return Verdict.ASK
    else:
        # Bare ``git`` or only safe boolean globals → harmless usage text.
        return Verdict.ALLOW

    sub = args[i].text
    if sub not in _GIT_READ_ONLY:
        return Verdict.ASK
    return _classify_git_subcommand(sub, args[i + 1 :])


def _classify_git_subcommand(sub: str, rest: list[_Word]) -> Verdict:
    """Read a read-only git subcommand's own arguments (#657)."""

    if sub == "config":
        return _classify_git_config(rest)
    if sub == "ls-remote":
        # Starts an ssh / upload-pack / transport helper — ASK unconditionally.
        return Verdict.ASK
    if sub == "branch":
        return _classify_git_listing(rest, _GIT_BRANCH_LONG, _GIT_BRANCH_SHORT)
    if sub == "tag":
        return _classify_git_listing(rest, _GIT_TAG_LONG, _GIT_TAG_SHORT)
    if sub == "remote":
        return _classify_git_remote(rest)
    if sub == "reflog" and rest and (rest[0].glob or rest[0].text in _GIT_REFLOG_WRITES):
        return Verdict.ASK
    return Verdict.ALLOW if _git_reads_only(sub, rest) else Verdict.ASK


def _git_reads_only(sub: str, rest: list[_Word]) -> bool:
    """No ``--output`` / ``--ext-diff`` (or a prefix) anywhere — every read-only subcommand.

    ``blame``, ``rev-list``, ``shortlog`` and ``reflog`` take ``--output`` too
    (verify V4: each truncated a file outside the project), so this is not
    limited to the diff family.

    Review round 2 (B4): ``--`` no longer ends the scan. A value-taking revision
    option swallows it (``git log --decorate-refs -- --output=x`` wrote), and
    ``git blame`` re-reads its legacy ``-- <path> <rev>`` form as options, so a
    ``--output`` after ``--`` still writes. A value option's value is still
    consumed (so ``git log -1 --grep --output`` greps for the text ``--output``
    and stays a read), and a glob asks only where it could expand into one of
    the write/exec options — before ``--``, since after it git takes the
    expansion as a pathspec. A pathspec literally spelled ``--output=…`` asking
    is the accepted cost.
    """

    values = _GIT_LOG_VALUE_OPTIONS if sub in _GIT_LOG_FAMILY else frozenset()
    output_capable = sub in _GIT_OUTPUT_SUBCOMMANDS
    seen_dashdash = False
    i = 0
    while i < len(rest):
        word = rest[i]
        i += 1
        if word.text == "--":
            seen_dashdash = True
            continue
        if _abbreviates(word.text.partition("=")[0], _GIT_WRITE_OR_EXEC_OPTIONS):
            return False
        if not seen_dashdash and word.text in values:
            if i < len(rest) and rest[i].glob:
                return False
            i += 1
            continue
        # A glob could expand into a planted ``--output=FILE`` the subcommand
        # would write to — but only if the subcommand HAS ``--output``, and only
        # before ``--`` (after it git takes the word as a pathspec; ``blame``
        # re-reads its legacy ``-- <path> <rev>`` form, so it still asks).
        if word.glob and output_capable and (not seen_dashdash or sub == "blame"):
            return False
    return True


def _resolve_git_long(key: str, table: dict[str, _GitOpt]) -> _GitOpt | None:
    """git parse-options lookup: the exact name, else a unique prefix, else ``None``."""

    if key in table:
        return table[key]
    hits = [name for name in table if name.startswith(key)]
    return table[hits[0]] if len(hits) == 1 else None


def _parse_git_options(
    words: list[_Word],
    longs: dict[str, _GitOpt],
    shorts: dict[str, _GitOpt],
    *,
    stop_at_operand: bool,
) -> tuple[list[_GitOpt], list[_Word]] | None:
    """Parse with ONE git command's option table: (options seen, operands).

    ``None`` — i.e. ASK — for an unsafe, unknown or ambiguous option, and for a
    glob that could expand into an option or a value. ``stop_at_operand`` is
    parse-options' ``STOP_AT_NON_OPTION``: legacy ``git config`` reads
    ``core.x v -l`` as three operands (a write), not as ``--list``.
    """

    seen: list[_GitOpt] = []
    operands: list[_Word] = []
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        tok = word.text
        if operands and stop_at_operand:
            operands.append(word)
            continue
        if tok == "--":
            operands.extend(words[i:])
            break
        if word.glob and word.may_be_option:
            return None
        if not tok.startswith("-") or tok == "-":
            operands.append(word)
            continue
        if tok.startswith("--"):
            key, eq, _ = tok.partition("=")
            spec = _resolve_git_long(key, longs)
            if spec is None or not spec.safe or (eq and not spec.value):
                return None
            seen.append(spec)
            if not eq and spec.value in ("required", "lastarg") and i < len(words):
                if words[i].glob:
                    return None
                i += 1
            continue
        for j, ch in enumerate(tok[1:], 1):
            spec = shorts.get(ch)
            if spec is None or not spec.safe:
                return None
            seen.append(spec)
            if spec.value:
                if spec.value != "optional" and j + 1 == len(tok) and i < len(words):
                    if words[i].glob:
                        return None
                    i += 1
                break
    return seen, operands


def _classify_git_config(rest: list[_Word]) -> Verdict:
    """``git config`` → ALLOW only a proven read shape (#657, review round 1).

    - ``get`` / ``list`` as the FIRST word is the new-style read (a subcommand
      anywhere else is a legacy key, measured);
    - legacy: a read action (``--get*``, ``-l``/``--list``, also in a cluster
      or abbreviated), or no action and exactly one ``section.key`` operand.
      Options stop at the first operand, so ``core.x v -l`` is a write.

    ``-e``, ``--comment``, every write action, ``-f``/``--file``/``--blob`` in
    any spelling (ADR-0288's arbitrary-file exclusion), an unknown option and a
    second operand with no read action all ask. A ``set`` / ``edit`` / … word is
    an operand with no ``.``, so it can never pass as a one-key read.
    """

    if any(word.glob for word in rest):
        return Verdict.ASK
    if rest and rest[0].text in ("get", "list"):
        longs = _GIT_CONFIG_GET_LONG if rest[0].text == "get" else _GIT_CONFIG_LIST_LONG
        parsed = _parse_git_options(
            rest[1:], longs, _GIT_CONFIG_SUBCOMMAND_SHORT, stop_at_operand=False
        )
        return Verdict.ASK if parsed is None else Verdict.ALLOW
    parsed = _parse_git_options(
        rest, _GIT_CONFIG_LEGACY_LONG, _GIT_CONFIG_LEGACY_SHORT, stop_at_operand=True
    )
    if parsed is None:
        return Verdict.ASK
    seen, operands = parsed
    if any(spec.marker for spec in seen) or not operands:
        return Verdict.ALLOW
    if len(operands) == 1 and "." in operands[0].text:
        return Verdict.ALLOW
    return Verdict.ASK


def _classify_git_listing(
    rest: list[_Word], longs: dict[str, _GitOpt], shorts: dict[str, _GitOpt]
) -> Verdict:
    """``git branch`` / ``git tag`` → ALLOW only a pure listing (#657).

    An operand is a branch/tag NAME to create unless a list-mode option is
    present (``-l``/``--list``, ``--contains``, ``--merged``, ``--points-at``,
    ``tag -n``), which makes it a PATTERN. ``-v``, ``--sort`` and ``--format``
    do NOT switch modes: ``git branch -v x`` creates ``x`` (measured).
    """

    parsed = _parse_git_options(rest, longs, shorts, stop_at_operand=False)
    if parsed is None:
        return Verdict.ASK
    seen, operands = parsed
    if operands and not any(spec.marker for spec in seen):
        return Verdict.ASK
    return Verdict.ALLOW


def _classify_git_remote(rest: list[_Word]) -> Verdict:
    """``git remote`` → ALLOW bare / ``-v``, ``get-url``, and ``show -n`` (no network).

    ``show`` without ``-n`` queries the remote; every other subcommand writes.
    """

    if any(word.glob for word in rest):
        return Verdict.ASK
    parsed = _parse_git_options(rest, _GIT_REMOTE_LONG, {"v": _OK}, stop_at_operand=True)
    if parsed is None:
        return Verdict.ASK
    operands = parsed[1]
    if not operands:
        return Verdict.ALLOW
    sub, args = operands[0].text, operands[1:]
    if sub == "get-url":
        found = _parse_git_options(args, _GIT_REMOTE_GET_URL_LONG, {}, stop_at_operand=False)
        return Verdict.ASK if found is None else Verdict.ALLOW
    if sub == "show":
        found = _parse_git_options(args, {}, {"n": _MARK}, stop_at_operand=False)
        if found is not None and any(spec.marker for spec in found[0]):
            return Verdict.ALLOW
    return Verdict.ASK


@dataclass(frozen=True)
class _SwitchGrammar:
    """ONE dialect's switch syntax for ONE ``_READ_ONLY`` command name (#204).

    An ALLOWLIST of read-only switches, never a denylist of write ones
    (ADR-0237). ``0be16cd``'s ``_READ_ONLY_ONLY_WHEN_BARE`` keyed on token
    SHAPE — ``-``-leading is a flag, ``/``-leading writes — and was measured
    wrong in BOTH directions on ``main`` c6d424c::

        ALLOW  sort -o out.txt in.txt          <- writes out.txt
        ALLOW  sort -oout.txt in.txt           <- GNU attached-value short form
        ALLOW  sort --output=out.txt in.txt
        ALLOW  sort --output out.txt in.txt
        ALLOW  sort --compress-program=/tmp/x in.txt   <- EXECUTES /tmp/x
        ALLOW  date --set=2030-01-01           <- sets the system clock
        ALLOW  hostname -b                     <- --boot: sets the hostname
        ASK    sort /etc/hosts                 <- a plain read, refused

    ``short_value`` chars consume the REST OF THEIR OWN TOKEN and end the
    cluster (``-k2``, ``-Iseconds``, ``-S10M``). A DETACHED value (``-k 2``,
    ``-T /tmp``) is deliberately NOT consumed: it falls through to the
    positional rule, which is this dialect's own answer for it and is strictly
    stricter — ``sort``'s positional is an input file so ``sort -k 2 in.txt``
    stays ALLOW (measured ALLOW today), while ``date -d yesterday`` stays ASK
    (measured ASK today) instead of the classifier inventing a value slot it
    cannot verify.
    """

    sigils: str
    short_bool: str = ""
    short_value: str = ""
    short_write: str = ""
    long_ok: frozenset[str] = frozenset()
    long_write: frozenset[str] = frozenset()
    long_exec: frozenset[str] = frozenset()
    switches_ok: frozenset[str] = frozenset()
    switches_write: frozenset[str] = frozenset()
    positional_mutates: bool = False
    plus_is_read_only: bool = False


# GNU coreutils 9.x ``sort``. ``-o``/``--output`` write; ``--compress-program``
# EXECUTES the named program on every temp file, which is worse than a write
# and was auto-allowed.
#
# ``--files0-from`` and ``--random-source`` are deliberately in NO set, so they
# land on the unknown-long-option ASK: each turns ONE argument into an unbounded
# set of reads, which is the identical shape and the identical wording that
# refuses ``findstr /f:``/``/g:`` in ``shell_classifiers.cmd``. Two classifiers
# stating opposite rules for the same hazard is how a table drifts.
_SORT_POSIX = _SwitchGrammar(
    sigils="-",
    short_bool="bcCdfghiMmnRrsuVz",
    short_value="kStN",
    short_write="o",
    long_ok=frozenset(
        {
            "--key",
            "--field-separator",
            "--buffer-size",
            "--temporary-directory",
            "--check",
            "--debug",
            "--dictionary-order",
            "--ignore-case",
            "--general-numeric-sort",
            "--human-numeric-sort",
            "--ignore-nonprinting",
            "--month-sort",
            "--numeric-sort",
            "--random-sort",
            "--reverse",
            "--sort",
            "--stable",
            "--unique",
            "--version-sort",
            "--zero-terminated",
            "--merge",
            "--parallel",
            "--help",
            "--version",
        }
    ),
    long_write=frozenset({"--output"}),
    long_exec=frozenset({"--compress-program"}),
)

# ``sort.exe``. ``/O`` names an output file and ``/T`` a temp directory, and
# neither produces a redirect node for :func:`_redirect_verdict` to see — the
# whole reason ``sort in.txt /o out.txt`` is a pinned ASK. A ``-``-leading
# token is a switch here too (ported GNU builds accept both sigils) and is in
# no allowlist, so it lands on the unknown-switch ASK.
_SORT_CMD = _SwitchGrammar(
    sigils="/-",
    switches_ok=frozenset({"/+", "/l", "/m", "/rec", "/r"}),
    switches_write=frozenset({"/o", "/t"}),
)

# GNU coreutils ``date``. A positional is the NEW CLOCK VALUE under BSD
# ``date`` and under cmd's ``date`` alike, which is why it mutates on every
# dialect; ``+FORMAT`` is a format string and stays read-only on all of them.
_DATE_POSIX = _SwitchGrammar(
    sigils="-",
    short_bool="uR",
    short_value="dfrI",
    short_write="s",
    long_ok=frozenset(
        {
            "--date",
            "--file",
            "--reference",
            "--iso-8601",
            "--rfc-3339",
            "--rfc-email",
            "--universal",
            "--utc",
            "--debug",
            "--help",
            "--version",
        }
    ),
    long_write=frozenset({"--set"}),
    positional_mutates=True,
    plus_is_read_only=True,
)

# cmd's ``date``: ``/T`` displays, a positional sets the clock, and bare
# ``date`` PROMPTS on stdin (``date.md``) — which the arg loop cannot see, so
# it is ALLOW here, and ``shell_classifiers.cmd``'s own ``date`` row refuses it.
_DATE_CMD = _SwitchGrammar(
    sigils="/",
    switches_ok=frozenset({"/t"}),
    positional_mutates=True,
    plus_is_read_only=True,
)

# ``hostname``: a positional renames the machine on every platform. ``-b``
# (``--boot``) and ``-F``/``--file`` SET it — ``-b`` measured ALLOW today.
# ``hostname.exe`` takes no switches at all, so CMD reuses this table: every
# ``/x`` becomes a mutating positional, i.e. ASK.
_HOSTNAME_POSIX = _SwitchGrammar(
    sigils="-",
    short_bool="sdfyaiI",
    short_write="bF",
    long_ok=frozenset(
        {
            "--fqdn",
            "--short",
            "--domain",
            "--all-fqdns",
            "--ip-address",
            "--all-ip-addresses",
            "--nis",
            "--yp",
            "--alias",
            "--help",
            "--version",
        }
    ),
    long_write=frozenset({"--file"}),
    positional_mutates=True,
)

_READ_ONLY_SWITCHES: dict[str, dict[Dialect, _SwitchGrammar]] = {
    "sort": {Dialect.POSIX: _SORT_POSIX, Dialect.CMD: _SORT_CMD},
    "date": {Dialect.POSIX: _DATE_POSIX, Dialect.CMD: _DATE_CMD},
    "hostname": {Dialect.POSIX: _HOSTNAME_POSIX, Dialect.CMD: _HOSTNAME_POSIX},
}
"""``_READ_ONLY`` names that are read-only by virtue of the PROGRAM, not the word.

The ALLOW tier decides on the command name alone, and it is the only tier whose
name-alone decision is permissive (``_DENY_COMMANDS`` / ``_ALWAYS_ASK_COMMANDS``
match the bare name too, but fail-safe). That asymmetry is sound only while the
name maps to a program whose ARGUMENTS someone read. These three are the names
where a dialect actually changes the answer.
"""

# Only POSIX and CMD get tables. The other two members are COMPOSED from them:
# ``POWERSHELL`` because ``pwsh`` ships on macOS and Linux where ``sort`` /
# ``date`` / ``hostname`` are the real GNU binaries, and on Windows where they
# are the same ``.exe`` cmd would run — so both dialects' write switches are
# refused (#204 F4). ``UNKNOWN`` is ADR-0158 strictest-wins as a default value.
_DIALECT_BASES: dict[Dialect, tuple[Dialect, ...]] = {
    Dialect.POSIX: (Dialect.POSIX,),
    Dialect.CMD: (Dialect.CMD,),
    Dialect.POWERSHELL: (Dialect.POSIX, Dialect.CMD),
    Dialect.UNKNOWN: (Dialect.POSIX, Dialect.CMD),
}

# Every dash codepoint a switch may be spelled with. PowerShell's own tokenizer
# accepts U+2013/2014/2015 as parameter dashes (``CharTraits.cs:12-15``,
# ``:254-259``, consumed at ``tokenizer.cs:4745-4749``, v7.4.6), so ``hostname
# –b`` is a real ``--boot``. Normalising is used ONLY to REFUSE (see
# :func:`_argument_is_read_only`): a homoglyph can make a token more suspicious,
# never less, because under a genuine POSIX shell ``date –u`` is a positional
# and a positional is what ``date`` sets its clock from.
_DASH_HOMOGLYPHS = str.maketrans({"–": "-", "—": "-", "―": "-"})


class _Tok(IntEnum):
    """How ONE dialect's grammar reads one argument token."""

    NOT_A_SWITCH = 0  # does not lead with one of THIS dialect's sigils
    OK = 1  # an allowlisted read-only switch
    UNRECOGNISED = 2  # leads with a sigil this dialect owns, but is in no table
    WRITE = 3  # an allowlisted WRITE or EXEC switch


def _cmd_switch_key(token: str) -> str:
    """Canonical form of a ``/x`` switch: casefolded, value stripped.

    cmd switches are case-insensitive (``sort /O`` == ``sort /o``) and take an
    attached value after ``:`` (``dir /a:h``). ``sort``'s ``/+n`` takes a column
    NUMBER with no separator, so the digits collapse into the ``/+`` key.
    """

    key = token.casefold().split(":", 1)[0]
    if key.startswith("/+") and key[2:].isdigit():
        return "/+"
    return key


def _read_switch(grammar: _SwitchGrammar, token: str) -> _Tok:
    """Read one token with ONE dialect's grammar. Never consults another."""

    if not token or token[0] not in grammar.sigils:
        return _Tok.NOT_A_SWITCH
    if token in ("-", "--"):
        # ``-`` is stdin and ``--`` ends the options; both read-only, both
        # measured ALLOW today (``sort -``).
        return _Tok.OK
    if token.startswith("--"):
        key = token.split("=", 1)[0]
        if key in grammar.long_write or key in grammar.long_exec:
            return _Tok.WRITE
        return _Tok.OK if key in grammar.long_ok else _Tok.UNRECOGNISED
    if token[0] == "-":
        # A cluster, walked left to right: ``-oout.txt`` is ``-o`` + a value and
        # must refuse, ``-k2`` and ``-Iseconds`` are a value-taking short option
        # and must not. NOT casefolded — GNU short options are case-sensitive
        # and ``sort -V`` / ``sort -R`` are distinct read-only options.
        for char in token[1:]:
            if char in grammar.short_write:
                return _Tok.WRITE
            if char in grammar.short_value:
                return _Tok.OK  # the rest of the token is this option's value
            if char not in grammar.short_bool:
                return _Tok.UNRECOGNISED
        return _Tok.OK
    key = _cmd_switch_key(token)
    if key in grammar.switches_write:
        return _Tok.WRITE
    return _Tok.OK if key in grammar.switches_ok else _Tok.UNRECOGNISED


def _switch_sigils(grammars: dict[Dialect, _SwitchGrammar], dialect: Dialect) -> str:
    """Which leading characters make a token a SWITCH rather than a positional.

    The one place ``POWERSHELL`` and ``UNKNOWN`` differ. Under ``pwsh`` a
    ``/``-leading token is a PATH — PowerShell's own parser has no ``/`` switch
    syntax — so ``sort /etc/hosts`` is a read. Under ``UNKNOWN`` we cannot know
    that, so ``/`` stays a sigil and an unrecognised ``/x`` is ASK, which is
    what keeps ``sort in.txt /o out.txt`` pinned (#204 criterion 2).
    """

    if dialect is Dialect.POWERSHELL:
        return grammars[Dialect.POSIX].sigils
    if dialect is Dialect.UNKNOWN:
        return "".join(g.sigils for g in grammars.values())
    return grammars[dialect].sigils


def _argument_is_read_only(
    grammars: dict[Dialect, _SwitchGrammar], dialect: Dialect, token: str
) -> bool:
    """Whether ``token`` is read-only under ``dialect``'s reading of this name.

    Three steps, in this order:

    1. **Refuse** if ANY base dialect calls the dash-normalised token a write or
       exec switch. A write switch of a dialect we might be is a write switch.
    2. Otherwise, if the RAW token leads with one of this dialect's sigils it is
       a switch: accept only if a base dialect that owns that sigil allowlists
       it. An unrecognised switch is ASK — "never ALLOW an argument we did not
       read".
    3. Otherwise it is a positional: accept unless a positional mutates under
       some base dialect.

    Step 1 reads the normalised text and steps 2–3 read the raw text on purpose:
    that is what makes a homoglyph dash strictly stricter. ``hostname –b`` (EN
    DASH) is refused as ``--boot`` at step 1, while ``date –u`` does not become
    an ALLOWed ``-u`` at step 2 — it stays the mutating positional a real POSIX
    shell would pass, i.e. ASK, which is its measured verdict today.
    """

    normalised = token.translate(_DASH_HOMOGLYPHS)
    bases = [grammars[d] for d in _DIALECT_BASES[dialect] if d in grammars]
    if any(_read_switch(g, normalised) is _Tok.WRITE for g in bases):
        return False
    if token.startswith("+"):
        # ``date +%Y`` is a FORMAT string, pinned ALLOW. Under cmd it is a
        # malformed ``MM-DD-YYYY`` that cmd rejects, so the worst case is an
        # error, not a mutation.
        return any(g.plus_is_read_only for g in bases)
    if token and token[0] in _switch_sigils(grammars, dialect):
        return any(
            _read_switch(g, token) is _Tok.OK for g in bases if token[0] in g.sigils
        )
    return not any(g.positional_mutates for g in bases)


def arguments_are_read_only(
    name: str, tokens: list[str | None], dialect: Dialect
) -> bool:
    """Whether EVERY token is read-only under ``dialect``'s reading of ``name``.

    Takes tokens rather than an AST node so the PowerShell classifier can reach
    the same tables (#204 §C, F4): under ``pwsh`` the names in
    :data:`_READ_ONLY_SWITCHES` are BOTH a cmdlet alias and a real binary
    depending on version and platform, so its ALLOW tier has to survive the
    binary reading too. A ``None`` token is dynamic and can never be proven
    non-mutating.
    """

    grammars = _READ_ONLY_SWITCHES[name]
    return all(
        token is not None and _argument_is_read_only(grammars, dialect, token)
        for token in tokens
    )


def _classify_dialect_read_only(command: Any, name: str, dialect: Dialect) -> Verdict:
    """ALLOW iff every argument is a token this DIALECT's grammar calls read-only.

    The replacement for ``0be16cd``'s ``_classify_bare_only_read_only``, whose
    two premises are both false on POSIX: *a ``-``-leading token is a read-only
    flag* (GNU ``date --set=``, ``date -s``, ``hostname -b``, ``hostname -F``
    all write) and *only a ``/``-leading token writes* (GNU ``sort -o`` writes
    and leads with ``-``). It paid a real POSIX read — ``sort /etc/hosts``,
    #204 criterion 5 — to catch a cmd switch while leaving the POSIX write
    switch of the same command wide open.
    """

    # A glob that may expand into a switch (``sort *`` beside a planted ``-o``)
    # is unknown, like an expansion (#657 review round 1).
    tokens: list[str | None] = []
    for arg in _command_args(command):
        word = _static_word(arg)
        tokens.append(None if word is None or (word.glob and word.may_be_option) else word.text)
    return (
        Verdict.ALLOW
        if arguments_are_read_only(name, tokens, dialect)
        else Verdict.ASK
    )


def _classify_find(command: Any, name: str) -> Verdict:
    """``find`` / ``fd`` → DENY if an exec/delete flag is present, else ALLOW.

    ``find`` and ``fd`` are NOT pure read-only commands: ``-delete`` /
    ``-exec`` / ``-execdir`` / ``-ok`` (find) and ``-x`` / ``--exec`` (fd)
    execute arbitrary commands or recursively delete files (finding WP-0 #2 —
    strictly worse than ``rm -rf`` because the regex Guardrail has no ``find``
    rule at all). A dynamic/unresolvable argument → ASK (we cannot prove it is
    not an exec flag); an absent exec flag → ALLOW (it is just a traversal).

    Arguments are static shell words (#657 review round 1): ``-de\\lete`` IS
    ``-delete``, and it deleted the tree while it read as ALLOW. A glob that
    could expand to a planted file named ``-delete`` asks, and so does an ``fd``
    short cluster holding ``x``/``X`` (clap reads ``-Hx`` as ``-H -x``). A
    dynamic word no longer stops the scan, so a later exec flag still DENYs.
    """

    exec_flags = _FIND_EXEC_FLAGS if name == "find" else _FD_EXEC_FLAGS
    verdict = Verdict.ALLOW
    for arg in _command_args(command):
        word = _static_word(arg)
        if word is None:
            # Dynamic argument — can't prove it isn't an exec/delete flag.
            verdict = Verdict.ASK
            continue
        text = word.text
        if (text.partition("=")[0] if text.startswith("--") else text) in exec_flags:
            return Verdict.DENY
        glob_hit = word.glob and (
            word.may_be_option if name == "fd" else any(map(word.could_be, exec_flags))
        )
        if glob_hit or (name == "fd" and _fd_cluster_execs(text)):
            verdict = Verdict.ASK
    return verdict


# fd's short options that take a value (sharkdp/fd clap): the rest of the
# cluster, or the next word, is their value — not a flag. So ``fd -tx`` is
# ``--type x`` (no ``-x`` exec), which the round-1 "any cluster with x/X asks"
# over-asked (should-fix).
_FD_SHORT_VALUES = frozenset("dtEecSj")


def _fd_cluster_execs(token: str) -> bool:
    """Whether an ``fd`` short cluster reaches ``-x`` / ``-X`` before a value option.

    clap reads ``-Hx`` as ``-H -x`` (exec → ASK) but ``-tx`` as ``-t x``
    (``x`` is the type value → safe). The exact ``-x`` / ``-X`` tokens are
    caught earlier by ``_FD_EXEC_FLAGS``; this reads a longer cluster.
    """

    if len(token) <= 2 or token[0] != "-" or token[1] == "-":
        return False
    for ch in token[1:]:
        if ch in "xX":
            return True
        if ch in _FD_SHORT_VALUES:
            return False  # the rest of the cluster is this option's value
    return False


def _classify_recursive_mutator(command: Any) -> Verdict:
    """``chmod``/``chown``/``mv``/``cp``/… → DENY a recursive op on ``/`` or ``~``.

    These filesystem mutators (finding WP-0 #5) are catastrophic only in their
    recursive form against a root/home target (``chmod -R 777 /``,
    ``chown -R user ~``). Such a form → DENY; anything else → ASK (never
    silent-ALLOW — they are mutating). A dynamic argument → ASK.
    """

    has_recursive = False
    targets_root_or_home = False
    for arg in _command_args(command):
        literal = _node_literal(arg)
        if literal is None:
            # A dynamic argument could be the recursive flag or a root target;
            # stay conservative.
            return Verdict.ASK
        if literal == "--recursive":
            has_recursive = True
            continue
        if literal.startswith("-") and not literal.startswith("--"):
            # A short-flag bundle (e.g. ``-Rf``); recursive is the bare ``-R``/``-r``.
            if "R" in literal[1:] or "r" in literal[1:]:
                has_recursive = True
            continue
        if literal.startswith("--"):
            continue  # other long flag (e.g. ``--preserve``)
        norm = literal.replace("\\", "/").rstrip("/")
        if norm in ("", "/", "~", "/*", "~/*") or norm.startswith("~/"):
            targets_root_or_home = True
    if has_recursive and targets_root_or_home:
        return Verdict.DENY
    return Verdict.ASK


def _redirect_verdict(redirect: Any) -> Verdict:
    """A write ``file_redirect`` to a protected path → DENY, else ASK.

    Only WRITE redirects gate (``>`` / ``>>`` / ``&>``); a read redirect
    (``<``) is benign. The target path literal is matched against the protected
    prefixes; a dynamic target → ASK.
    """

    write_ops = (">", ">>", "&>", ">&", ">|")
    read_ops = ("<", "<<", "<<<", "<&")
    op = ""
    target: Any | None = None
    for child in redirect.children:
        ct = child.type
        if ct in write_ops or ct in read_ops:
            op = ct
        elif ct not in ("&", "|"):
            target = child
    if not op or op in read_ops:
        return Verdict.ALLOW  # read redirect — benign
    if target is None:
        return Verdict.ASK
    path = _node_literal(target)
    if path is None:
        return Verdict.ASK
    norm = path.replace("\\", "/")
    for prefix in _PROTECTED_WRITE_PREFIXES:
        if norm == prefix or norm.startswith(prefix.rstrip("/") + "/"):
            return Verdict.DENY
    return Verdict.ASK


def _walk_redirect(node: Any, *, dialect: Dialect) -> Verdict:
    """A redirect's own verdict, worst-of everything nested in it.

    An unquoted here-document body, a here-string and a redirect target are
    expanded before the command runs: ``cat <<EOF`` + ``$(rm -rf ~)`` runs the
    ``rm`` and ``true <<EOF`` + ``$((GIT_CONFIG_COUNT=0))`` assigns (#657
    review round 1, measured; ALLOW since ADR-0158). A quoted ``<<'EOF'`` body
    is not expanded.

    Review round 2: tree-sitter-bash puts the rest of a here-document's first
    line INSIDE the ``heredoc_redirect`` — ``cat <<EOF | sh``, ``cat <<EOF >
    ~/.bashrc`` and ``cat <<EOF && rm -rf /`` hold the pipeline, the redirect or
    the command as a child — and none of them was walked (ALLOW since
    ADR-0158). And a body tree-sitter leaves as one unparsed leaf
    (``<<-EOF`` + a tab, a line indented with spaces) hid its expansions from
    every check: :func:`_heredoc_body_unread` asks on it, and a re-read of the
    body still finds a DENY inside it.
    """

    if node.type == "file_redirect":
        worst = _redirect_verdict(node)
        for child in node.children:
            if _contains_executable_node(child):
                worst = max(worst, _walk(child, stage_index=0, dialect=dialect))
        return worst
    worst = Verdict.ALLOW
    if node.type == "heredoc_redirect" and _heredoc_body_unread(node):
        worst = max(Verdict.ASK, _reread_heredoc_body(node, dialect=dialect))
    expands = _heredoc_expands(node)
    for child in node.children:
        if not child.is_named or child.type in ("heredoc_start", "heredoc_end"):
            continue
        if child.type == "heredoc_body" and not expands:
            continue
        worst = max(worst, _walk(child, stage_index=0, dialect=dialect))
    return worst


# What can follow ``$`` and start an expansion; a ``$`` before anything else is
# a literal dollar sign.
_EXPANSION_START = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{([@*#?$!-")


def _heredoc_body_unread(redirect: Any) -> bool:
    """Whether an expanded here-document body holds a ``$`` or backtick tree-sitter did not parse."""

    if not _heredoc_expands(redirect):
        return False
    covered = _ranges(redirect, _EXPANSION_NODES)
    for body in redirect.children:
        if body.type != "heredoc_body":
            continue
        text = body.text
        i = 0
        while i < len(text):
            ch = text[i : i + 1]
            if ch == b"\\":
                i += 2
                continue
            starts = ch == b"`" or (ch == b"$" and text[i + 1 : i + 2] and text[i + 1] in _EXPANSION_START)
            at = body.start_byte + i
            if starts and not any(lo <= at < hi for lo, hi in covered):
                return True
            i += 1
    return False


def _reread_heredoc_body(redirect: Any, *, dialect: Dialect) -> Verdict:
    """Classify an unparsed body as the double-quoted string it expands like.

    Only to find a DENY the leaf hid (``$(rm -rf ~)``); the caller asks anyway.
    A ``"`` is literal in a body and is escaped for the re-read.
    """

    if _PARSER is None:
        return Verdict.ASK
    bodies = b"".join(c.text for c in redirect.children if c.type == "heredoc_body")
    text = b': "' + bodies.replace(b'"', b'\\"') + b'"'
    reread = _PARSER.parse(text).root_node
    if reread.has_error:
        return Verdict.ASK
    return _walk(reread, dialect=dialect)


# Node types that wrap (or are) an executable subtree worth re-walking when they
# appear as a command ARGUMENT (finding WP-0 #1).
_EXECUTABLE_WRAPPER_TYPES = frozenset(
    {"command_substitution", "process_substitution"}
)


def _contains_executable_node(node: Any) -> bool:
    """Whether ``node`` is/contains a command- or process-substitution.

    Used to decide if a command argument must be re-walked as a fresh statement:
    a plain ``word`` / literal argument has nothing to execute and is skipped (so
    benign args like ``ls -la`` are NOT pushed through the ASK-biased catch-all),
    but ``$(…)`` / ``<(…)`` — even nested inside a ``string`` / ``concatenation``
    — is walked so its embedded payload is classified.
    """

    if not getattr(node, "is_named", False):
        return False
    if node.type in _EXECUTABLE_WRAPPER_TYPES:
        return True
    return any(_contains_executable_node(child) for child in node.children)


def _walk(node: Any, *, stage_index: int = 0, dialect: Dialect) -> Verdict:
    """Recursive worst-of traversal. ``stage_index`` tracks pipeline position.

    ``stage_index`` is the 0-based position of a ``command`` within its
    enclosing ``pipeline`` — a shell appearing at a non-first stage
    (``curl … | sh``) is the pipe-into-shell DENY case.

    ``dialect`` (#204) is threaded through untouched and consumed only by
    :func:`_classify_simple_command`; it is keyword-only and has no default so
    a new recursion site cannot silently fall back to ``UNKNOWN``.
    """

    t = node.type

    if t in _CONTROL_FLOW:
        return Verdict.ASK

    if t in _ASSIGNMENT_STATEMENTS:
        # #657 decision 3, statement form (review round 1, V2): ``export
        # GIT_DIR=cache; git status``, ``GIT_CONFIG_KEY_0=…; git status`` and
        # ``unset GIT_CONFIG_COUNT`` set or clear what the next command reads,
        # exactly as a ``VAR=value`` prefix does. A payload in the value still
        # DENYs (``export A=$(rm -rf /)``).
        #
        # Review round 2 should-fix: a ``declaration_command`` with only flag
        # words and no name or assignment is PRINT-ONLY — ``declare -p``,
        # ``export -p``, bare ``export``, ``declare -x``, ``typeset -f`` list
        # and change nothing — so it floors at ALLOW (``_GUARD_NAME`` in the
        # source still catches a ``GIT_*`` name, and a payload still DENYs).
        print_only = node.type == "declaration_command" and not any(
            child.type in ("variable_assignment", "variable_name") for child in node.children
        )
        worst = Verdict.ALLOW if print_only else Verdict.ASK
        for child in node.children:
            if child.is_named:
                worst = max(worst, _walk(child, stage_index=stage_index, dialect=dialect))
        return worst

    if t == "command":
        base = _classify_simple_command(node, dialect)
        # Pipe-into-shell: a shell command at a non-first pipeline stage executes
        # whatever was piped in → DENY regardless of the per-command verdict.
        if stage_index > 0:
            name_node = _command_name_node(node)
            if name_node is not None:
                inner = name_node.children[0] if name_node.children else name_node
                literal = _node_literal(inner)
                if literal is not None and _normalize_command_name(literal) in _SHELLS:
                    return Verdict.DENY
        # CRITICAL (finding WP-0 #1): a command-substitution / process-substitution
        # nested as an ARGUMENT to an allowlisted command (``ls $(rm -rf /)``,
        # ``cat <(curl x|sh)``) executes its embedded payload at runtime, yet the
        # name-only ``_classify_simple_command`` never inspects it. Walk every
        # argument child as a FRESH statement (stage_index reset to 0 so the
        # embedded pipeline's own pipe-into-shell / denylist verdicts apply) and
        # take the worst — so the embedded payload's DENY bubbles up via max().
        #
        # #657 review round 1: every child, not only the arguments — a
        # ``VAR=$(…)`` prefix and a redirect target run their payload too
        # (``A=$(rm -rf /) ls``).
        #
        # Review round 2: a redirect written before the name (``> /etc/x echo
        # hi``) is a child of the command, not of a ``redirected_statement``,
        # and was never judged (ALLOW since ADR-0158).
        worst = base
        for child in node.children:
            if child.type in _REDIRECT_NODES or _contains_executable_node(child):
                worst = max(worst, _walk(child, stage_index=0, dialect=dialect))
        return worst

    if t == "pipeline":
        worst = Verdict.ALLOW
        idx = 0
        for child in node.children:
            if child.type == "|" or child.type == "|&":
                idx += 1
                continue
            worst = max(worst, _walk(child, stage_index=idx, dialect=dialect))
        return worst

    if t in _REDIRECT_NODES:
        return _walk_redirect(node, dialect=dialect)

    if t == "redirected_statement":
        worst = Verdict.ALLOW
        for child in node.children:
            worst = max(worst, _walk(child, stage_index=stage_index, dialect=dialect))
        return worst

    if t in (
        "list",
        "program",
        "subshell",
        "compound_statement",
        "command_substitution",
        "process_substitution",
        "negated_command",
    ):
        worst = Verdict.ALLOW
        for child in node.children:
            # Skip pure punctuation / operator tokens.
            if not child.is_named:
                continue
            worst = max(worst, _walk(child, stage_index=stage_index, dialect=dialect))
        return worst

    # Any other named node we recurse into; leaf / unrecognized → ALLOW (a
    # bubble-up neutral element — real risk lives in command/redirect nodes).
    # Defense-in-depth (finding WP-0 #6): for an UNRECOGNIZED named node that
    # wraps an executable subtree (a future grammar bump introducing a new
    # command-wrapping node type) bias the floor toward ASK, not ALLOW — an
    # auto-ALLOW classifier must fail safe on unknown STRUCTURE.
    if node.is_named and node.children:
        worst = Verdict.ASK if _contains_executable_node(node) else Verdict.ALLOW
        for child in node.children:
            if not child.is_named:
                continue
            worst = max(worst, _walk(child, stage_index=stage_index, dialect=dialect))
        return worst
    return Verdict.ALLOW


def classify(command: str, *, dialect: Dialect = Dialect.UNKNOWN) -> Verdict:
    """Classify ``command`` into ALLOW / ASK / DENY (fail-safe to ASK).

    Returns :data:`Verdict.ASK` for an empty command, when the grammar is
    unavailable, when parsing yields ``has_error`` (malformed / partial input),
    or on any unexpected exception during traversal — NEVER ALLOW on
    uncertainty.

    ``dialect`` (#204, ADR-0237) says which shell's SWITCH SYNTAX a known
    read-only name's arguments are read with; the bash GRAMMAR is used
    regardless, which is why ``is_classifiable_shell`` still gates the ALLOW at
    the permission site. The default is ``UNKNOWN``, not ``POSIX``: a POSIX
    default reads ``sort in.txt /o out.txt``'s ``/o`` as a path and returns
    ALLOW, which is the fail-open shape #204 criterion 2 forbids and would
    require rewriting the pinned assertions to keep them "passing".
    """

    if not command or not command.strip():
        return Verdict.ASK
    if _PARSER is None:
        return Verdict.ASK
    try:
        source = command.encode()
        tree = _PARSER.parse(source)
        root = tree.root_node
        if root.has_error:
            return Verdict.ASK
        verdict = _walk(root, dialect=dialect)
        if verdict is Verdict.ALLOW and _evaluates_while_expanding(root, source):
            return Verdict.ASK  # #657 decision 3 inside an expansion
        return verdict
    except Exception:  # noqa: BLE001 — any traversal failure → fail safe
        return Verdict.ASK


def classifier_available() -> bool:
    """Whether the tree-sitter grammar loaded (AUTO can auto-allow/deny)."""

    return _PARSER is not None


__all__ = [
    "Verdict",
    "arguments_are_read_only",
    "classifier_available",
    "classify",
]
