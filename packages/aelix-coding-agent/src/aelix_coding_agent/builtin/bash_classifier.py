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

from enum import IntEnum
from typing import Any


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


def _classify_simple_command(command: Any) -> Verdict:
    """Classify a single ``command`` node (allowlist / denylist / git rules)."""

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


def _walk(node: Any, *, stage_index: int = 0) -> Verdict:
    """Recursive worst-of traversal. ``stage_index`` tracks pipeline position.

    ``stage_index`` is the 0-based position of a ``command`` within its
    enclosing ``pipeline`` — a shell appearing at a non-first stage
    (``curl … | sh``) is the pipe-into-shell DENY case.
    """

    t = node.type

    if t in _CONTROL_FLOW:
        return Verdict.ASK

    if t == "command":
        base = _classify_simple_command(node)
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
                worst = max(worst, _walk(arg, stage_index=0))
        return worst

    if t == "pipeline":
        worst = Verdict.ALLOW
        idx = 0
        for child in node.children:
            if child.type == "|" or child.type == "|&":
                idx += 1
                continue
            worst = max(worst, _walk(child, stage_index=idx))
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
                worst = max(worst, _walk(child, stage_index=stage_index))
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
            worst = max(worst, _walk(child, stage_index=stage_index))
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
            worst = max(worst, _walk(child, stage_index=stage_index))
        return worst
    return Verdict.ALLOW


def classify(command: str) -> Verdict:
    """Classify ``command`` into ALLOW / ASK / DENY (fail-safe to ASK).

    Returns :data:`Verdict.ASK` for an empty command, when the grammar is
    unavailable, when parsing yields ``has_error`` (malformed / partial input),
    or on any unexpected exception during traversal — NEVER ALLOW on
    uncertainty.
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
        return _walk(root)
    except Exception:  # noqa: BLE001 — any traversal failure → fail safe
        return Verdict.ASK


def classifier_available() -> bool:
    """Whether the tree-sitter grammar loaded (AUTO can auto-allow/deny)."""

    return _PARSER is not None


__all__ = ["Verdict", "classifier_available", "classify"]
