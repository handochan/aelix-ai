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
from contextvars import ContextVar
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


# Every operator tree-sitter-bash emits as a clean ``file_redirect`` token that
# OPENS A FILE for writing. ``&>>`` (append stdout+stderr, bash 4 / zsh) was
# missing, so ``echo … &>> ~/.ssh/authorized_keys`` was ALLOW while the ``>>``
# form was DENY (#677). A numbered form (``2>>``, ``1>|``) carries the same
# operator token with the fd in a separate child, so it is covered. ``>&-`` /
# ``<&-`` close a descriptor and open no file (no target); ``&>|`` / ``>>|`` /
# ``<>`` are parse errors tree-sitter reports as ``has_error`` → the gate
# already ASKs. ``>&`` / ``<&`` with a descriptor number are fd dups (no file);
# until #710 the number after ``>&`` was read as a path.
_WRITE_REDIRECT_OPS = frozenset({">", ">>", "&>", "&>>", ">&", ">|"})
_READ_REDIRECT_OPS = frozenset({"<", "<<", "<<<", "<&"})
# tree-sitter emits a close as ONE token with the ``-`` inside it (#710).
_CLOSE_REDIRECT_OPS = frozenset({">&-", "<&-"})
_DUP_REDIRECT_OPS = frozenset({">&", "<&"})
# The standard sink devices: a write redirect to one changes no file (#678).
_SINK_DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})
# The bytes that end an unquoted shell word: bash's blanks and metacharacters.
# tree-sitter also skips ``\r``, ``\v`` and ``\f`` between tokens, but bash and
# zsh keep them in the word (``echo x >&1\r`` writes a file ``1\r``), so they
# are not here (#710 fix round 1). A backtick ends a word only where it closes
# the substitution the word is in (:func:`_ends_a_word`, fix round 2).
_WORD_ENDS = frozenset(b" \t\n;&|()<>")
_BLANKS = frozenset(b" \t")


# The source of the tree being walked, set by :func:`_walk_source` (#710 fix
# round 1). :func:`_redirect_verdict` reads the bytes AFTER a target, which no
# node covers (a trailing ``\r`` belongs only to the root), and climbing to the
# root costs tree-sitter a descent from the root per step.
_SOURCE: ContextVar[bytes | None] = ContextVar("bash_classifier_source", default=None)


def _walk_source(root: Any, source: bytes, *, dialect: Dialect) -> Verdict:
    """:func:`_walk` over a tree parsed from ``source``, worst-of its argument views.

    ``source`` is readable by the redirects under ``root`` while they are
    walked. #710 fix round 1: tree-sitter keeps every word after a redirect's
    target inside the redirect, where no argument check sees it, while bash,
    zsh, dash and ksh pass those words to the command (measured;
    ``find . 2>&1 -delete -maxdepth 1`` deletes). When a redirect holds one,
    :func:`_argument_view` is walked as well, so the verdict is never below the
    command's with those words as its arguments; a view the grammar rejects is
    ASK.

    Fix round 2: each tree also answers :func:`_evaluates_while_expanding`,
    through :func:`_judge`. Its ``test`` / ``[`` reading takes the command's
    arguments, which skip redirect nodes, so only the view shows it the
    operands of ``test 2>&1 -v 'a[$(cmd)]'`` (bash 5 runs ``cmd``, measured)
    or ``test </dev/null -v …``; asked of the original tree alone, both were
    ALLOW.

    Finish round: a view only RAISES. A redirect that holds a word some shell
    passes to the command (:func:`_passes_words`) makes the verdict at least
    ASK whatever the views say, because tree-sitter's reading of such a line
    does not always match the shells' (``echo hi &>/dev/null find . -delete``,
    ``test 1 -eq 0>&1 'a[$(cmd)]'``; both ran unasked when a view could lower
    the verdict to ALLOW). Under the UNKNOWN and POSIX dialects the words after
    an ``&>`` target are also judged as a command of their own, as dash and
    ksh run them (:func:`_argument_view` with ``background``).
    """

    verdict = _judge(root, source, dialect=dialect)
    redirects, digits = _scan_redirects(root)
    if not redirects:
        return verdict
    if any(_passes_words(redirect) for redirect in redirects):
        verdict = max(verdict, Verdict.ASK)
    views = [_view_of(source, redirects, digits, background=False)]
    if dialect in (Dialect.UNKNOWN, Dialect.POSIX):
        views.append(_view_of(source, redirects, digits, background=True))
    for view in views:
        if view is None:
            continue
        if _PARSER is None:
            return max(verdict, Verdict.ASK)
        reread = _PARSER.parse(view).root_node
        if reread.has_error:
            verdict = max(verdict, Verdict.ASK)
        else:
            verdict = max(verdict, _judge(reread, view, dialect=dialect))
    return verdict


def _judge(root: Any, source: bytes, *, dialect: Dialect) -> Verdict:
    """One tree's verdict: :func:`_walk` with ``source`` readable, then the expansion question.

    An ALLOW whose source could assign, or evaluate a word as a name or as
    arithmetic, while expanding is ASK (#657 decision 3). A here-document
    inside a command or process substitution makes it at least ASK
    (:func:`_substituted_heredocs_verdict`, #742 fix round 1), and so does
    code tree-sitter left as text in a ``${…}`` operand or across a backtick
    (:func:`_hidden_code_verdict`, #742 fix round 2).
    """

    token = _SOURCE.set(source)
    try:
        verdict = _walk(root, dialect=dialect)
    finally:
        _SOURCE.reset(token)
    if b"<<" in source:
        verdict = max(verdict, _substituted_heredocs_verdict(root, source, dialect=dialect))
    if b"${" in source or b"`" in source:
        verdict = max(verdict, _hidden_code_verdict(root, source, dialect=dialect))
    if verdict is Verdict.ALLOW and _evaluates_while_expanding(root, source):
        return Verdict.ASK  # #657 decision 3 inside an expansion
    return verdict


def _tree_source(node: Any) -> bytes:
    """The whole source ``node`` was parsed from.

    The one :func:`_walk_source` set, when ``node`` is in it; otherwise (a
    direct call) its root's text, which spans every byte of the input.
    """

    source = _SOURCE.get()
    if source is not None and source[node.start_byte : node.end_byte] == node.text:
        return source
    while (parent := getattr(node, "parent", None)) is not None:
        node = parent
    return node.text or b""


def _ends_a_word(source: bytes, at: int, node: Any) -> bool:
    """Whether the shell's word ends at byte ``at`` of ``node``'s command.

    At the end of input, a blank or a metacharacter, or at the backtick that
    closes a ``\\`…\\``` substitution ``node`` is in: the shell runs what the
    backticks hold as a command of its own (fix round 2; ``echo \\`ls
    2>/dev/null\\``` writes no file in bash 3.2, zsh, dash or ksh, measured).
    Any other backtick starts a substitution whose output joins the word:
    ``echo x >&1\\`echo .txt\\``` writes ``1.txt`` in bash and zsh, and
    ``2>&-\\`echo 1\\``` writes ``-1`` in zsh.
    """

    if at >= len(source) or source[at] in _WORD_ENDS:
        return True
    return source[at : at + 1] == b"`" and _closes_its_substitution(node, at)


def _closes_its_substitution(node: Any, at: int) -> bool:
    """Whether byte ``at`` is the closing backtick of the substitution nearest above ``node``."""

    while (node := getattr(node, "parent", None)) is not None:
        if node.type == "command_substitution":
            last = node.children[-1] if node.children else None
            return last is not None and last.type == "`" and last.start_byte == at
    return False


def _redirect_operator(redirect: Any) -> Any | None:
    """The one operator token of a ``file_redirect``, or ``None`` if it has none or several."""

    ops = [child for child in redirect.children if not child.is_named]
    return ops[0] if len(ops) == 1 else None


def _redirect_arguments(redirect: Any) -> list[Any]:
    """The words a redirect node holds that the shell passes to the command (#710 fix round 1).

    tree-sitter-bash puts every word after a redirect INSIDE its node:
    ``find . 2>&1 -delete -maxdepth 1`` is ``file_redirect(2 >& 1 -delete
    -maxdepth 1)`` and ``find . <<EOF -delete`` holds ``-delete`` in the
    ``heredoc_redirect``. bash, zsh, dash and ksh take the FIRST word as the
    target and pass every further one to the command (measured), so those
    words are arguments; a close (``>&-``) has no target, so all of them are.
    A here-string takes one word and has none. A redirect nested in a
    here-document's line is a node of its own and answers for itself.
    """

    if redirect.type == "file_redirect":
        words = list(redirect.children_by_field_name("destination"))
        op = _redirect_operator(redirect)
        if op is not None and op.type in _CLOSE_REDIRECT_OPS:
            return words
        return words[1:]
    if redirect.type == "heredoc_redirect":
        return list(redirect.children_by_field_name("argument"))
    return []


def _passes_words(redirect: Any) -> bool:
    """Whether a redirect node holds a word some shell passes to the command (#710 finish).

    The words after its target (:func:`_redirect_arguments`), or a descriptor
    of two or more digits: bash takes ``10>&1`` as descriptor 10, while zsh,
    dash and ksh take only one digit there and pass ``10`` on (``echo a 10>&1
    x`` prints ``a x`` in bash and ``a 10 x`` in the others, macOS and Linux,
    measured), so ``git branch 10>&1`` creates a branch ``10`` in zsh.
    """

    if _redirect_arguments(redirect):
        return True
    descriptor = redirect.children_by_field_name("descriptor")
    return bool(descriptor) and len(descriptor[0].text) > 1


def _scan_redirects(root: Any) -> tuple[list[Any], dict[int, Any]]:
    """Every redirect node under ``root``, and every all-digit ``number`` keyed by the byte it ends at."""

    redirects: list[Any] = []
    digits: dict[int, Any] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in _REDIRECT_NODES:
            redirects.append(node)
        elif node.type == "number" and node.text.isdigit():
            digits[node.end_byte] = node
    return redirects, digits


def _glued_descriptor(redirect: Any, digits: dict[int, Any], source: bytes) -> Any | None:
    """The all-digit word right before a ``<`` / ``>`` operator, when tree-sitter left it outside.

    #710 finish: one digit written with no blank before ``<`` or ``>`` is that
    redirect's descriptor in bash, zsh, dash and ksh (``echo a 0>&1 x`` prints
    ``a x`` in all four on macOS and Linux, measured), but tree-sitter makes a
    ``0`` there a ``number`` argument of the command, or the command's name in
    ``0>&1 mkfs /dev/sda``; ``1`` to ``9`` and longer runs it takes as the
    descriptor, and ``00`` or ``01`` do not parse. Only a whole shell word
    counts: not one joined to other text (``$(echo a)0>&1`` passes ``a0``),
    and not one before ``&>``, which starts with ``&``.
    """

    op = next((child for child in redirect.children if not child.is_named), None)
    if op is None or not op.type.startswith(("<", ">")):
        return None
    word = digits.get(redirect.start_byte)
    if word is None or getattr(word.parent, "type", None) == "concatenation":
        return None
    start = word.start_byte
    if start == 0 or source[start - 1] in _WORD_ENDS or source[start - 1 : start] == b"`":
        return word
    return None


def _redirect_verdict(redirect: Any) -> Verdict:
    """A ``file_redirect``'s own verdict, from its operator and its FIRST destination word.

    A write to a protected path → DENY, any other write → ASK; a read (``<``)
    opens no file for writing and is ALLOW. The target path literal is matched
    against the protected prefixes; a dynamic target → ASK. The words after the
    target are the command's arguments, judged through :func:`_argument_view`,
    not here.

    #710: ``2>&1``, ``>&2`` and ``3>&1`` duplicate a descriptor and open no file
    in bash, zsh, dash or ksh (measured), yet the ``number`` after ``>&`` was
    read as a path and ASKed, so ``cat a 2>&1`` opened a dialog under ``auto``.
    A ``>&`` / ``<&`` target is a dup only when its source bytes, from the
    operator to the end of the shell word, are blanks and then ASCII digits and
    nothing else: tree-sitter's ``number`` also covers ``0x1``, ``1#x`` and
    ``16#ff``, which bash and zsh write as files; tree-sitter skips a ``\\r``,
    ``\\v`` or ``\\f`` that bash and zsh keep in the word (``>&1\\r`` writes a
    file ``1\\r``); and after ``>`` / ``&>`` a number is a file name
    (``echo x >1``). Any other ``<&`` target is path-checked like a write's
    (fix round 1; it was ALLOW whatever it named).

    A bare close (``2>&-``) opens no file and is ALLOW. A word glued to it
    (``2>&-1``, ``2>&-\\r``) is ASK: zsh writes it as a file ``-1``. A sink
    device is ALLOW only when nothing is glued to it either. An operator this
    function does not know is ASK.
    """

    op_node = _redirect_operator(redirect)
    if op_node is None:
        return Verdict.ASK
    op = op_node.type
    source = _tree_source(redirect)
    if op in _CLOSE_REDIRECT_OPS:
        return Verdict.ALLOW if _ends_a_word(source, op_node.end_byte, redirect) else Verdict.ASK
    if op not in _WRITE_REDIRECT_OPS and op not in _READ_REDIRECT_OPS:
        return Verdict.ASK
    targets = redirect.children_by_field_name("destination")
    if not targets:
        return Verdict.ASK
    target = targets[0]
    if op in _READ_REDIRECT_OPS and op not in _DUP_REDIRECT_OPS:
        return Verdict.ALLOW  # a read redirect — no file written
    whole = all(b in _BLANKS for b in source[op_node.end_byte : target.start_byte]) and (
        _ends_a_word(source, target.end_byte, redirect)
    )
    if op in _DUP_REDIRECT_OPS and whole and target.type == "number" and target.text.isdigit():
        return Verdict.ALLOW  # a dup; ``bytes.isdigit`` is ASCII-only
    path = _node_literal(target)
    if path is None:
        return Verdict.ASK
    norm = path.replace("\\", "/")
    # A write redirect to the three standard sink devices changes no file, so it
    # is ALLOW as a REDIRECT (the command's own verdict, with any words written
    # after the target among its arguments since #710, still applies) — ``/dev``
    # being a protected prefix otherwise refused ``rg foo src 2>/dev/null`` and
    # ``git log … 2>/dev/null | head``, common reads (#678). Anything else under
    # ``/dev`` (``/dev/sda``, …) stays DENY.
    if whole and norm in _SINK_DEVICES:
        return Verdict.ALLOW
    for prefix in _PROTECTED_WRITE_PREFIXES:
        if norm == prefix or norm.startswith(prefix.rstrip("/") + "/"):
            return Verdict.DENY
    return Verdict.ASK


# What a here-document redirect is made of besides the words, redirects or
# pipeline that follow it on its first line.
_HEREDOC_PARTS = frozenset(
    {"file_descriptor", "<<", "<<-", "heredoc_start", "heredoc_body", "heredoc_end"}
)


def _redirect_blanks(redirect: Any) -> list[tuple[int, int]]:
    """The byte ranges of a redirect that are no part of the command line it sits in.

    Its descriptor, operator and target — for a here-document also its
    delimiters and body, for a here-string all of it. What is left is what the
    shell passes on: the words after the target, and whatever a here-document's
    first line goes on to hold. A ``$(…)`` / ``<(…)`` inside a blanked part is
    kept in place, because it runs (``cat <<<$(find . 2>&1 -delete)``); one in
    a quoted here-document body does not run and is blanked with it.
    """

    if redirect.type == "heredoc_redirect":
        parts = [c for c in redirect.children if c.type in _HEREDOC_PARTS]
        blanks = [(c.start_byte, c.end_byte) for c in parts]
        expands = _heredoc_expands(redirect)
        runs = [c for c in parts if c.type == "heredoc_body" and expands]
    elif redirect.type == "file_redirect":
        op = _redirect_operator(redirect)
        targets = redirect.children_by_field_name("destination")
        if op is not None and op.type in _CLOSE_REDIRECT_OPS:
            end = op.end_byte  # no target: every word is passed on
        else:
            end = targets[0].end_byte if targets else redirect.end_byte
        blanks = [(redirect.start_byte, end)]
        runs = [c for c in redirect.children if c.end_byte <= end]
    else:  # a here-string: its one word
        blanks = [(redirect.start_byte, redirect.end_byte)]
        runs = [redirect]
    kept = sorted(r for node in runs for r in _ranges(node, _EXECUTABLE_WRAPPER_TYPES))
    out: list[tuple[int, int]] = []
    for lo, hi in blanks:
        for keep_lo, keep_hi in kept:
            if lo < keep_hi and keep_lo < hi:
                if lo < keep_lo:
                    out.append((lo, keep_lo))
                lo = keep_hi
        if lo < hi:
            out.append((lo, hi))
    return out


def _argument_view(root: Any, source: bytes, *, background: bool = False) -> bytes | None:
    """``source`` with every redirect blanked out but the words it passes on (#710 fix round 1).

    ``None`` when no redirect holds such a word, which is nearly always. The
    blanks keep every other byte where it was, so the view parses into the same
    statements, pipelines and lists with the redirects' words as arguments of
    the command bash gives them to — tree-sitter hangs ``ls | find . 2>&1
    -delete``'s redirect on the whole pipeline, bash gives it to ``find``, and
    the view reads ``ls | find .      -delete``. A compound command followed by
    words (``{ ls; } 2>&1 x``, which bash refuses) does not parse as a view.

    Finish round: a digit word glued before a ``<`` / ``>`` operator is the
    redirect's descriptor (:func:`_glued_descriptor`) and is blanked too, so
    ``0>&1 mkfs /dev/sda`` reads ``     mkfs /dev/sda``. With ``background``,
    the ``&`` of an ``&>`` / ``&>>`` that holds words after its target is
    kept: dash and macOS ksh93 read ``&>`` as ``&`` and then ``>``, so
    ``echo hi &>/dev/null find . -delete`` runs ``echo hi`` in the background
    and then ``find . -delete`` (measured: both deleted), and that view reads
    ``echo hi &          find . -delete``. ``None`` with ``background`` when
    no such ``&>`` exists.
    """

    redirects, digits = _scan_redirects(root)
    return _view_of(source, redirects, digits, background=background)


def _view_of(
    source: bytes, redirects: list[Any], digits: dict[int, Any], *, background: bool
) -> bytes | None:
    """:func:`_argument_view` from a :func:`_scan_redirects` already made."""

    glued = [word for r in redirects if (word := _glued_descriptor(r, digits, source)) is not None]
    passing = [r for r in redirects if _redirect_arguments(r)]
    amps = [
        op
        for r in passing
        if (op := _redirect_operator(r)) is not None and op.type in ("&>", "&>>")
    ]
    if (background and not amps) or not (passing or glued):
        return None
    view = bytearray(source)
    for redirect in redirects:
        for lo, hi in _redirect_blanks(redirect):
            view[lo:hi] = b" " * (hi - lo)
    for word in glued:
        view[word.start_byte : word.end_byte] = b" " * (word.end_byte - word.start_byte)
    if background:
        for op in amps:
            view[op.start_byte] = ord("&")
    return bytes(view)


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
    body still finds a DENY inside it. A delimiter tree-sitter ran on past is
    judged by :func:`_heredoc_start_verdict` (#710 fix round 2), and a body
    the shells end on another line than tree-sitter does by
    :func:`_heredoc_end_verdict` (#742).
    """

    if node.type == "file_redirect":
        worst = _redirect_verdict(node)
        for child in node.children:
            if _contains_executable_node(child):
                worst = max(worst, _walk(child, stage_index=0, dialect=dialect))
        return worst
    worst = Verdict.ALLOW
    if node.type == "heredoc_redirect":
        worst = max(
            _heredoc_start_verdict(node, dialect=dialect),
            _heredoc_end_verdict(node, dialect=dialect),
        )
        if _heredoc_body_unread(node):
            worst = max(worst, Verdict.ASK, _reread_heredoc_body(node, dialect=dialect))
    expands = _heredoc_expands(node)
    for child in node.children:
        if not child.is_named or child.type in ("heredoc_start", "heredoc_end"):
            continue
        if child.type == "heredoc_body" and not expands:
            continue
        worst = max(worst, _walk(child, stage_index=0, dialect=dialect))
    return worst


# A here-document delimiter as the shells read it: one word of plain bytes,
# quoted runs and backslash escapes, with no blank or metacharacter outside the
# quotes. ``$`` and backticks are left out: no shell expands a delimiter, and
# leaving them out costs an ASK, never an ALLOW.
_HEREDOC_DELIMITER = re.compile(rb"""(?:[^\s;&|()<>'"\\`$]|\\[^\n]|'[^']*'|"[^"\\`$]*")+""")


def _heredoc_start_verdict(redirect: Any, *, dialect: Dialect) -> Verdict:
    """ALLOW for a delimiter that is one word; otherwise ASK, or worse for what it swallowed.

    #710 fix round 2 (pre-existing since ADR-0158): tree-sitter's
    ``heredoc_start`` runs on to the next blank without a parse error, so
    ``cat <<A; rm -rf ~`` is a start ``A;`` and ``cat <<A>~/.bashrc`` a start
    ``A>~/.bashrc``. bash, zsh, dash and ksh end the delimiter at the ``;`` or
    ``>`` and run the rest of the line (measured: ``touch``, ``find victim
    -delete`` and the ``.bashrc`` write all happened, with the end line written
    as ``A;``). The rest of that line, from the first byte the delimiter cannot
    hold to the newline, is re-read after a ``:`` so a DENY in it still counts.
    """

    start = next((c for c in redirect.children if c.type == "heredoc_start"), None)
    if start is None:
        return Verdict.ASK
    match = _HEREDOC_DELIMITER.match(start.text)
    if match is not None and match.end() == len(start.text):
        return Verdict.ALLOW
    if _PARSER is None:
        return Verdict.ASK
    source = _tree_source(redirect)
    begin = start.start_byte + (match.end() if match is not None else 0)
    newline = source.find(b"\n", start.end_byte)
    text = b": " + source[begin : newline if newline != -1 else len(source)]
    reread = _PARSER.parse(text).root_node
    if reread.has_error:
        return Verdict.ASK
    return max(Verdict.ASK, _walk_source(reread, text, dialect=dialect))


# --- #742: the line the shells end a here-document body on ---
#
# tree-sitter-bash 0.25.1 and the shells disagree on which line ends a body.
# tree-sitter compares the line with the RAW delimiter unless it starts with a
# quote or backslash (``<<A''`` ends at a line ``A''``), and it also ends at a
# line that only holds the delimiter among blanks or before ``;``, ``#…`` or
# ``\r`` (``EOF ``, `` EOF``, ``EOF;``). bash, zsh, dash and ksh end at the
# first line equal to the delimiter after quote removal (``A``), with leading
# tabs removed under ``<<-``, and, when no part of the delimiter is quoted,
# with a line ending in an odd number of backslashes joined to the next. A
# body tree-sitter ends too late hides the commands after the shell's end; one
# it ends too early lets what follows open another here-document that hides
# the line the shells run next (measured on macOS and Linux:
# ``.omc/probes/742/impl/``).

# One reading per shell family of ``$'…'`` / ``$"…"`` in a delimiter, measured
# on macOS and Linux: dash keeps the ``$`` (``$'A'`` ends at ``$A``); bash, zsh
# and ksh93 decode ``$'…'``; ``$"…"`` ends at ``A`` in bash, ``$A`` in zsh and
# dash, and at the raw ``$"A"`` in ksh93.
_DOLLAR_QUOTE_READINGS = (
    ("keep", "keep"),  # dash
    ("decode", "drop"),  # bash
    ("decode", "keep"),  # zsh
    ("decode", "raw"),  # ksh93
)

# Inside double quotes a backslash is removed only before these; before a
# newline it is removed with the newline.
_DQ_ESCAPE = re.compile(rb'\\([$`"\\\n])')

# The bytes the #742 checks may still scan or re-read in this :func:`classify`
# call. A re-read walks the here-documents in it again, so unbounded, 16
# mismatched ones parsed 6,632 times the source (measured), and each
# here-document's search for the shells' end line scans the rest of the source
# (fix round 1: Codex's ``cat <<A''`` + ``x`` + ``B`` + ``ls`` + ``A''`` times
# 1,600 scanned 26.9 MB). Every scanned line and every re-read is charged its
# length; work the budget cannot pay is skipped and leaves the verdict at ASK,
# never lower. ``None`` (a direct call, not through :func:`classify`) does not
# limit.
_REREAD_BUDGET: ContextVar[list[int] | None] = ContextVar(
    "bash_classifier_reread_budget", default=None
)
_REREAD_PASSES = 4
_REREAD_SLACK = 4096
# How many re-reads may run inside one another. Each nests a walk on the
# stack, so a run of mismatched here-documents re-read inside each other ran
# out of stack (fix round 1: Codex's 103,172-byte input); past the limit a
# re-read is not made and the verdict stays at ASK.
_REREAD_DEPTH: ContextVar[int] = ContextVar("bash_classifier_reread_depth", default=0)
_REREAD_DEPTH_LIMIT = 24
# What :func:`_shell_end_line` returns when the budget could not pay its scan.
_UNPAID = (-1, -1)


def _charge_reread(size: int) -> bool:
    """Take ``size`` bytes from the re-read budget; ``False`` when it cannot pay them."""

    budget = _REREAD_BUDGET.get()
    if budget is None:
        return True
    if budget[0] < size:
        return False
    budget[0] -= size
    return True


def _reread(text: bytes, *, dialect: Dialect, expanded: bool = False) -> Verdict:
    """Classify ``text`` the shells run, paid from the budget; ASK when it cannot be read.

    ``expanded`` reads it as an expanded here-document body
    (:func:`_reread_as_expanded`), otherwise as command lines
    (:func:`_reread_lines`). A re-read the budget cannot pay, one nested
    deeper than :data:`_REREAD_DEPTH_LIMIT`, and one that runs out of stack
    are ASK here, so the walk that asked for it keeps any DENY it already
    found (fix round 1: a ``RecursionError`` in a re-read reached
    :func:`classify`'s catch and turned a visible ``find victim -delete``'s
    DENY into ASK).
    """

    depth = _REREAD_DEPTH.get()
    if depth >= _REREAD_DEPTH_LIMIT or not _charge_reread(len(text)):
        return Verdict.ASK
    token = _REREAD_DEPTH.set(depth + 1)
    try:
        if expanded:
            return _reread_as_expanded(text, dialect=dialect)
        return _reread_lines(text, dialect=dialect)
    except RecursionError:
        return Verdict.ASK
    finally:
        _REREAD_DEPTH.reset(token)


def _shell_word_end(source: bytes, at: int) -> int | None:
    """Where the shell word starting at ``at`` ends, or ``None`` if a quote is left open.

    ``None`` too when the budget cannot pay the bytes scanned (fix round 1).
    """

    budget = _REREAD_BUDGET.get()
    stop = len(source) if budget is None else min(len(source), at + budget[0])
    i = at
    quote = 0
    while i < stop:
        ch = source[i]
        if quote == ord("'"):
            quote = 0 if ch == quote else quote
        elif quote == ord('"'):
            if ch == ord("\\"):
                i += 1
            elif ch == quote:
                quote = 0
        elif ch in _WORD_ENDS:
            break
        elif ch == ord("\\"):
            i += 1
        elif ch in b"'\"":
            quote = ch
        i += 1
    end = min(i, len(source))
    if quote or (end >= stop and stop < len(source)) or not _charge_reread(end - at):
        return None
    return end


def _delimiter_readings(word: bytes) -> frozenset[bytes] | None:
    """The delimiter each shell family reads ``word`` as, or ``None`` when one is unknown.

    Quote removal: ``'…'`` keeps its bytes, ``"…"`` drops a backslash before
    ``$``, `````, ``"``, ``\\`` or a newline, and an unquoted backslash
    quotes the next byte. ``$'…'`` and ``$"…"`` follow
    :data:`_DOLLAR_QUOTE_READINGS`; a backslash inside ``$'…'`` (an escape
    to decode) is unknown, and so is a byte above ASCII (macOS ksh93 ended
    ``<<É''`` and ``<<ÉOF`` at no line, measured).
    """

    if any(byte > 0x7F for byte in word):
        return None
    readings: set[bytes] = set()
    for ansi, locale in _DOLLAR_QUOTE_READINGS:
        out = bytearray()
        i = 0
        while i < len(word):
            ch = word[i : i + 1]
            dollar = ch == b"$" and word[i + 1 : i + 2] in (b"'", b'"')
            quote = word[i + 1 : i + 2] if dollar else ch
            if quote not in (b"'", b'"'):
                if ch == b"\\":
                    out += word[i + 1 : i + 2].replace(b"\n", b"")
                    i += 2
                    continue
                out += ch
                i += 1
                continue
            begin = i + 2 if dollar else i + 1
            end = word.find(quote, begin) if quote == b"'" else _double_quote_end(word, begin)
            if not begin <= end < len(word):
                return None  # a quote left open
            inner = word[begin:end]
            if quote == b'"':
                inner = _DQ_ESCAPE.sub(lambda m: m.group(1).replace(b"\n", b""), inner)
            if not dollar:
                out += inner
            elif quote == b"'":
                if ansi == "keep":
                    out += b"$" + inner
                elif b"\\" in inner:
                    return None
                else:
                    out += inner
            elif locale == "raw":
                out += word[i : end + 1]
            else:
                out += (b"$" if locale == "keep" else b"") + inner
            i = end + 1
        readings.add(bytes(out))
    return frozenset(readings)


def _double_quote_end(word: bytes, begin: int) -> int:
    """The index of the ``"`` closing a double-quoted run that starts at ``begin``."""

    i = begin
    while word[i : i + 1] not in (b'"', b""):
        i += 2 if word[i : i + 1] == b"\\" else 1
    return i


def _line_start(source: bytes, at: int) -> int:
    """The index of the first byte of the line holding ``source[at]``."""

    return source.rfind(b"\n", 0, at) + 1


def _shell_end_line(
    source: bytes, begin: int, delimiters: frozenset[bytes], *, strip_tabs: bool, joins: bool
) -> tuple[int, int] | None:
    """The ``(start, end)`` bytes of the first line from ``begin`` the shells end the body at.

    ``None`` when no line matches: the shells read the body to the end of the
    input. ``joins`` glues a line ending in an odd number of backslashes to
    the next, as the shells do for a delimiter with no quoted part (measured:
    ``x\\`` then ``EOF`` is not an end, ``x\\\\`` then ``EOF`` is).
    :data:`_UNPAID` when :data:`_REREAD_BUDGET` cannot pay the bytes scanned
    (fix round 1: one scan per here-document over the rest of the source made
    the work quadratic).
    """

    budget = _REREAD_BUDGET.get()
    at = begin
    while at < len(source):
        if budget is not None and budget[0] <= 0:
            return _UNPAID
        end = source.find(b"\n", at)
        end = len(source) if end == -1 else end
        line = source[at:end]
        while joins and end < len(source) and (len(line) - len(line.rstrip(b"\\"))) % 2:
            nxt = source.find(b"\n", end + 1)
            nxt = len(source) if nxt == -1 else nxt
            line = line[:-1] + source[end + 1 : nxt]
            end = nxt
        if not _charge_reread(end + 1 - at):
            return _UNPAID
        if (line.lstrip(b"\t") if strip_tabs else line) in delimiters:
            return at, end
        at = end + 1
    return None


def _heredoc_end_verdict(redirect: Any, *, dialect: Dialect) -> Verdict:
    """ALLOW when the shells end the body on tree-sitter's end line; otherwise ASK or worse.

    #742 (pre-existing since ADR-0158): ``cat <<A''`` + ``x`` + ``A`` +
    ``find victim -delete`` + ``A''`` was ALLOW — tree-sitter ends the body at
    ``A''``, so the ``find`` sat in a quoted body nobody walks, while bash,
    zsh, dash and ksh end it at ``A`` and delete ``victim`` (measured on
    macOS and Linux). Each shell family's delimiter (:func:`_delimiter_readings`)
    is looked for line by line from the body's first line
    (:func:`_shell_end_line`); a reading that ends anywhere but on
    tree-sitter's end line makes the verdict at least ASK, and what that shell
    runs is re-read so a DENY in it still counts (:func:`_heredoc_end_rereads`).
    A delimiter whose readings are unknown asks, and so does a scan the budget
    cannot pay. Fix round 1: running out of stack here is ASK too, so the walk
    keeps any DENY it already found.
    """

    try:
        return _heredoc_end_reads(redirect, dialect=dialect)
    except RecursionError:
        return Verdict.ASK


def _heredoc_end_reads(redirect: Any, *, dialect: Dialect) -> Verdict:
    """:func:`_heredoc_end_verdict` without its stack guard."""

    kids = {child.type: child for child in reversed(redirect.children)}
    start = kids.get("heredoc_start")
    if start is None:
        return Verdict.ASK
    source = _tree_source(redirect)
    word_end = _shell_word_end(source, start.start_byte)
    if word_end is None:
        return Verdict.ASK
    word = source[start.start_byte : word_end]
    readings = _delimiter_readings(word)
    if readings is None:
        return Verdict.ASK
    end = kids.get("heredoc_end")
    first = kids.get("heredoc_body") or end
    if first is not None:
        begin = _line_start(source, first.start_byte)
    else:
        newline = source.find(b"\n", start.end_byte)
        begin = len(source) if newline == -1 else newline + 1
    ts_end: tuple[int, int] | None = None
    if end is not None:
        newline = source.find(b"\n", end.end_byte)
        ts_end = (_line_start(source, end.start_byte), len(source) if newline == -1 else newline)
    strip_tabs = any(child.type == "<<-" for child in redirect.children)
    quoted = any(ch in word for ch in b"'\"\\")
    shell_ends = {
        _shell_end_line(source, begin, frozenset({d}), strip_tabs=strip_tabs, joins=not quoted)
        for d in readings
    }
    if _UNPAID in shell_ends:
        return Verdict.ASK
    worst = Verdict.ALLOW
    for shell_end in shell_ends - {ts_end}:
        worst = max(
            worst,
            Verdict.ASK,
            _heredoc_end_rereads(source, begin, shell_end, ts_end, quoted=quoted, dialect=dialect),
        )
    return worst


def _heredoc_end_rereads(
    source: bytes,
    begin: int,
    shell_end: tuple[int, int] | None,
    ts_end: tuple[int, int] | None,
    *,
    quoted: bool,
    dialect: Dialect,
) -> Verdict:
    """What one shell runs when it ends the body on ``shell_end``, re-read through :func:`_reread`.

    The shell's body as the double-quoted string it expands like (unquoted
    delimiter only), the lines after its end up to tree-sitter's end line
    (they parse inside ``$(…)`` too), and everything after its end.
    """

    rereads: list[tuple[bytes, bool]] = []
    if not quoted:
        rereads.append((source[begin : shell_end[0] if shell_end else len(source)], True))
    if shell_end is not None:
        after = shell_end[1] + 1
        if ts_end is not None and ts_end[0] > shell_end[0]:
            rereads.append((source[after : ts_end[1]], False))
        rereads.append((source[after:], False))
    worst = Verdict.ASK
    for text, expanded in rereads:
        worst = max(worst, _reread(text, dialect=dialect, expanded=expanded))
    return worst


def _reread_lines(text: bytes, *, dialect: Dialect) -> Verdict:
    """Classify lines the shells run as commands; ASK at least.

    Fix round 1: when ``text`` does not parse, its prefix that ends before
    the first statement or line tree-sitter cannot parse (or, when that is
    the first, before its last line) is classified instead, each shorter
    prefix paid from :data:`_REREAD_BUDGET`. bash, zsh,
    dash and ksh ran ``find victim -delete`` before an unended ``cat <<Z``
    (Codex: ``cat <<A''`` + ``x`` + ``A`` + ``find victim -delete`` + ``cat
    <<Z`` + ``A''`` deleted ``victim`` in all four and was ASK, not DENY).
    """

    if _PARSER is None:
        return Verdict.ASK
    while text:
        reread = _PARSER.parse(text).root_node
        if not reread.has_error:
            return max(Verdict.ASK, _walk_source(reread, text, dialect=dialect))
        statement = next((c.start_byte for c in reread.children if c.has_error), 0)
        cuts = [c for c in (statement, _line_start(text, _error_byte(reread))) if 0 < c < len(text)]
        text = text[: max(cuts) if cuts else _line_start(text, len(text) - 1)]
        if not _charge_reread(len(text)):
            break
    return Verdict.ASK


def _error_byte(root: Any) -> int:
    """Where the first part of ``root`` that tree-sitter could not parse starts.

    The innermost node with an error in it; in an ``ERROR`` node whose parts
    each parse, its first stray token (``\\``` in ``find victim -delete`` +
    ``Q`` + ``\\```).
    """

    node = root
    while (bad := next((c for c in node.children if c.has_error), None)) is not None:
        node = bad
    if node.type == "ERROR":
        return next((c.start_byte for c in node.children if not c.is_named), node.start_byte)
    return node.start_byte


# --- #742 fix round 1: a here-document inside a substitution ---
#
# Inside backticks, bash, zsh, dash and ksh on macOS and Linux end the
# substitution at its first unescaped backtick, inside a here-document body
# too, and remove ``\⏎`` before they read the here-document, whatever its
# delimiter. macOS bash 3.2.57 does both with a ``)`` inside ``$( )`` and
# ``<( )`` (the round-1 verify, ``.omc/probes/742/verify-r1/``; R1-R4 deleted
# ``victim`` and were ALLOW). tree-sitter knows neither. So any
# here-document in a command or process substitution makes the verdict at
# least ASK, and the text the shells read instead is re-read so a DENY in it
# still counts. The cost is ASK on a harmless one: ``echo `cat <<'EOF'`` +
# ``hi`` + ``EOF``` `` and ``echo $(cat <<EOF`` + ``hi`` + ``EOF`` + ``)``.

# A backslash inside backticks is removed before ``$``, ````` ``, ``\\`` and
# a newline (with the newline).
_BACKTICK_ESCAPE = re.compile(rb"\\([$`\\\n])")


def _substituted_heredocs_verdict(root: Any, source: bytes, *, dialect: Dialect) -> Verdict:
    """ASK at least for each command / process substitution holding a here-document, or worse.

    For each one :func:`_substitution_rereads` re-reads what the shells read
    instead. Running out of stack here is ASK, so the walk's DENY is kept.
    """

    try:
        worst = Verdict.ALLOW
        for sub, heredocs in _substituted_heredocs(root):
            worst = max(worst, Verdict.ASK, _substitution_rereads(sub, heredocs, source, dialect))
        return worst
    except RecursionError:
        return Verdict.ASK


def _substituted_heredocs(root: Any) -> list[tuple[Any, list[Any]]]:
    """Each command / process substitution under ``root`` with the here-documents inside it.

    A here-document counts for every substitution around it, nested ones too.
    """

    found: dict[tuple[int, int], tuple[Any, list[Any]]] = {}
    stack: list[tuple[Any, tuple[Any, ...]]] = [(root, ())]
    while stack:
        node, around = stack.pop()
        if node.type in _EXECUTABLE_WRAPPER_TYPES:
            around = (*around, node)
        elif node.type == "heredoc_redirect":
            for sub in around:
                found.setdefault((sub.start_byte, sub.end_byte), (sub, []))[1].append(node)
        stack.extend((child, around) for child in node.children)
    return list(found.values())


def _substitution_rereads(
    sub: Any, heredocs: list[Any], source: bytes, dialect: Dialect
) -> Verdict:
    """ASK, or worse for what the shells read as ``sub`` and after it.

    Backticks: the text up to the first unescaped backtick with ``\\$``,
    ``\\```, ``\\\\`` and ``\\⏎`` unescaped. ``$( )``, ``<( )``, ``>( )``,
    as macOS bash 3.2 reads them: the text up to the first ``)`` a
    here-document's body or end line holds outside nested parentheses, with
    ``\\⏎`` joined. That text is re-read when it is not what tree-sitter
    read. When the shells end the substitution before tree-sitter does, the
    source is re-read too with the substitution, up to where they end it,
    replaced by one word: what follows it stays on its command line, or in
    its double-quoted string (``echo $(cat <<'EOF'`` + ``1) find victim
    -delete`` passes ``find`` to ``echo`` in bash 3.2, measured). Each
    re-read is paid from :data:`_REREAD_BUDGET` through :func:`_reread`;
    what it cannot pay stays ASK.
    """

    kids = sub.children
    if len(kids) < 2:
        return Verdict.ASK
    start = kids[0].end_byte
    stop = kids[-1].start_byte if kids[-1].type in ("`", ")") else sub.end_byte
    if not _charge_reread(stop - start):
        return Verdict.ASK
    if kids[0].type == "`":
        close = _first_unescaped(source, start, stop, b"`")
        inner = _BACKTICK_ESCAPE.sub(lambda m: m.group(1).replace(b"\n", b""), source[start:close])
    else:
        parts = [c.start_byte for h in heredocs for c in h.children if c.type in _HEREDOC_LINES]
        close = _first_unescaped(source, min(parts, default=stop), stop, b")")
        inner = source[start:close].replace(b"\\\n", b"")
    worst = Verdict.ASK
    if inner != source[start:stop]:
        worst = max(worst, _reread(inner, dialect=dialect))
    if close < stop:
        view = source[: sub.start_byte] + b"x" + source[close + 1 :]
        worst = max(worst, _reread(view, dialect=dialect))
    return worst


# The parts of a here-document that sit on the lines after its start.
_HEREDOC_LINES = frozenset({"heredoc_body", "heredoc_end"})


def _first_unescaped(source: bytes, start: int, stop: int, close: bytes) -> int:
    """The first ``close`` byte in ``source[start:stop]`` no backslash escapes, else ``stop``.

    For ``)``, one that closes a ``(`` opened in the range does not count.
    """

    depth = 0
    i = start
    while i < stop:
        ch = source[i : i + 1]
        if ch == b"\\":
            i += 2
            continue
        if ch == close and depth == 0:
            return i
        if close == b")" and ch in b"()":
            depth += 1 if ch == b"(" else -1
        i += 1
    return stop


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

    bodies = b"".join(c.text for c in redirect.children if c.type == "heredoc_body")
    return _reread_as_expanded(bodies, dialect=dialect)


def _reread_as_expanded(body: bytes, *, dialect: Dialect) -> Verdict:
    """Classify an expanded here-document ``body`` as the double-quoted string it expands like."""

    if _PARSER is None:
        return Verdict.ASK
    text = b': "' + body.replace(b'"', b'\\"') + b'"'
    reread = _PARSER.parse(text).root_node
    if reread.has_error:
        return Verdict.ASK
    return _walk_source(reread, text, dialect=dialect)


# --- #742 fix round 2: code the parser leaves as text ---
#
# tree-sitter-bash 0.25.1 keeps some code as text, where no walk sees it, and
# the shells run it (fix round 2, ``.omc/probes/742/fix-r2/``):
#
# - an operand of ``${…}``: a ``word`` when a backtick substitution in it
#   holds no quote (``${x:-`find victim -delete`}``), a ``regex`` after
#   ``#``, ``%``, ``/``, ``^`` or ``,`` whatever it holds
#   (``${x#$(find victim -delete)}``), and ``'…'`` or ``$'…'``, which
#   tree-sitter reads as literal but the shells do not inside double quotes
#   or an expanded here-document body (``"${x:-'$(find victim -delete)'}"``);
# - inside backticks, a ``'…'`` or ``$'…'`` holding a backtick: bash and
#   ksh93 end the substitution at that backtick, while tree-sitter reads the
#   string across it (``echo `echo '`; find victim -delete; echo `'```); and
#   a comment holding one, which every shell ends there and tree-sitter
#   reads to the end of the line.
#
# Fix round 3 (the round-3 verify's measurements, ``.omc/probes/742/verify-r3/``):
#
# - a ``<(…)`` or ``>(…)`` in a ``${…}`` operand, which tree-sitter reads as
#   a plain word and bash runs (``echo ${x:-<(find victim -delete)}`` in
#   macOS bash 3.2.57 and Linux bash 5.2.37; no other shell measured ran
#   it, Alpine bash 5.3.20 included);
# - inside backticks, a word holding an escaped backtick, which the shells
#   unescape into a nested substitution (``echo `echo \`find victim
#   -delete\``` in all nine shells measured).
#
# Each asks at least, and the text is re-read as the shells read it, as far
# as the budget pays, so a DENY in it still counts.

# A backtick, ``$(``, ``$[``, ``${``, ``<(`` or ``>(`` (group 1) or a
# backslash escape.
_HIDDEN_CODE = re.compile(rb"\\.|(`|\$[({[]|[<>]\()", re.DOTALL)
# Where a ``${…}`` sits decides how a quote inside its operand reads: the
# nearest enclosing node that is not one of ``_QUOTE_TRANSPARENT``.
_DOUBLE_QUOTED = frozenset({"string", "heredoc_body"})
_QUOTE_TRANSPARENT = frozenset({"expansion", "concatenation"})
# Text tree-sitter reads across a backtick the shells end a substitution at.
_BACKTICK_CUT_TEXT = frozenset({"raw_string", "ansi_c_string", "comment"})


def _hides_code(text: bytes) -> bool:
    """Whether ``text`` holds a backtick, ``$(``, ``$[``, ``${``, ``<(`` or ``>(`` no backslash escapes."""

    return any(match.group(1) for match in _HIDDEN_CODE.finditer(text))


def _has_backtick(text: bytes) -> bool:
    """Whether ``text`` holds a backtick no backslash escapes."""

    return any(match.group(1) == b"`" for match in _HIDDEN_CODE.finditer(text))


def _hidden_code_verdict(root: Any, source: bytes, *, dialect: Dialect) -> Verdict:
    """ASK at least for code tree-sitter left as text in a ``${…}`` operand or across a backtick.

    Worse when the text, re-read as the shells read it, is; each re-read is
    made by :func:`_reread`, which answers ASK where it runs out of stack.
    """

    worst = Verdict.ALLOW
    # Each node carries whether a ``${…}`` there is in a double-quoted string
    # or a here-document body. Fix round 3 carries it down: climbing
    # ``.parent``, which py-tree-sitter answers by walking down from the root,
    # cost cubic time in nested operands (``echo ${x:-${x:-…}}`` 490 deep).
    stack = [(root, False)]
    while stack:
        node, quoted = stack.pop()
        if node.type == "expansion":
            worst = max(worst, _expansion_text_verdict(node, dialect, quoted=quoted))
        elif node.type == "command_substitution" and node.children[0].type == "`":
            worst = max(worst, _backtick_text_verdict(node, source, dialect))
        if node.type not in _QUOTE_TRANSPARENT:
            quoted = node.type in _DOUBLE_QUOTED
        stack.extend((child, quoted) for child in node.children)
    return worst


def _expansion_text_verdict(expansion: Any, dialect: Dialect, *, quoted: bool) -> Verdict:
    """ASK or worse when an operand of ``expansion`` holds code as text (:func:`_hides_code`).

    The text is re-read as a double-quoted string when the ``${…}`` is in
    one or in a here-document body (``quoted``), else as a word (where a
    ``'…'`` or ``$'…'`` quotes). Substitutions tree-sitter parsed are
    walked already, and a ``${…}`` inside is judged on its own.
    """

    worst = Verdict.ALLOW
    stack = list(expansion.children)
    while stack:
        node = stack.pop()
        if node.type in ("expansion", *_EXECUTABLE_WRAPPER_TYPES):
            continue
        if node.children:
            stack.extend(node.children)
            continue
        if not node.is_named or not _hides_code(node.text):
            continue
        if quoted:
            reread = _reread(node.text, dialect=dialect, expanded=True)
        else:
            reread = _reread(b": " + node.text, dialect=dialect)
        worst = max(worst, Verdict.ASK, reread)
    return worst


def _backtick_text_verdict(sub: Any, source: bytes, dialect: Dialect) -> Verdict:
    """ASK or worse when a ``'…'``, ``$'…'`` or comment inside backticks holds a backtick, or a word an escaped one.

    What the shells read, up to the first unescaped backtick and with an
    escaped one unescaped, is re-read by :func:`_substitution_rereads`.
    """

    stack = list(sub.children)
    while stack:
        node = stack.pop()
        if (node.type in _BACKTICK_CUT_TEXT and _has_backtick(node.text)) or (
            node.type == "word" and b"\\`" in node.text
        ):
            return max(Verdict.ASK, _substitution_rereads(sub, [], source, dialect))
        stack.extend(node.children)
    return Verdict.ALLOW


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
        token = _REREAD_BUDGET.set([_REREAD_PASSES * len(source) + _REREAD_SLACK])
        try:
            return _walk_source(root, source, dialect=dialect)
        finally:
            _REREAD_BUDGET.reset(token)
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
