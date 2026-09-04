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
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprintf", "-fls", "-fprint"}
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

    from aelix_coding_agent.tools.bash import shell_basename  # noqa: PLC0415

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


def _classify_simple_command(command: Any, dialect: Dialect) -> Verdict:
    """Classify a single ``command`` node (allowlist / denylist / git rules).

    ``dialect`` reaches exactly ONE decision point — the ALLOW tier's
    :func:`_classify_dialect_read_only` (#204). Every other branch here is a
    fact about a command NAME, which no dialect changes.
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
        return Verdict.ALLOW
    # Unknown command → ASK (never silent-allow).
    return Verdict.ASK


_GIT_VALUE_FLAGS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)


def _classify_git(command: Any) -> Verdict:
    """git X → ALLOW iff X is a read-only subcommand, else ASK.

    Value-bearing global flags (``-C``, ``-c``, ``--git-dir``, …) consume the
    following token (the path/value) before the subcommand is resolved, so
    ``git -C . status`` correctly reaches ``status`` → ALLOW (nit WP-0 #3).
    """

    skip_next = False
    for arg in _command_args(command):
        literal = _node_literal(arg)
        if literal is None:
            return Verdict.ASK
        if skip_next:
            skip_next = False  # this token is the value of a value-bearing flag
            continue
        if literal in _GIT_VALUE_FLAGS:
            skip_next = True
            continue
        if literal.startswith("-"):
            # ``--git-dir=...`` carries its value inline; bare boolean flag.
            continue
        return Verdict.ALLOW if literal in _GIT_READ_ONLY else Verdict.ASK
    # Bare ``git`` with no subcommand → harmless usage text.
    return Verdict.ALLOW


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

    tokens = [_node_literal(arg) for arg in _command_args(command)]
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
    """

    exec_flags = _FIND_EXEC_FLAGS if name == "find" else _FD_EXEC_FLAGS
    for arg in _command_args(command):
        literal = _node_literal(arg)
        if literal is None:
            # Dynamic argument — can't prove it isn't an exec/delete flag.
            return Verdict.ASK
        if literal in exec_flags:
            return Verdict.DENY
    return Verdict.ALLOW


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
        worst = base
        for arg in _command_args(node):
            if _contains_executable_node(arg):
                worst = max(worst, _walk(arg, stage_index=0, dialect=dialect))
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

    if t == "redirected_statement":
        worst = Verdict.ALLOW
        for child in node.children:
            if child.type in (
                "file_redirect",
                "heredoc_redirect",
                "herestring_redirect",
            ):
                worst = max(worst, _redirect_verdict(child))
            else:
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
        tree = _PARSER.parse(command.encode())
        root = tree.root_node
        if root.has_error:
            return Verdict.ASK
        return _walk(root, dialect=dialect)
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
