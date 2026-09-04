"""PowerShell dialect classifier (#204, ADR-0237).

The sister of ``bash_classifier`` for ``pwsh``/``powershell``: a
``tree-sitter-powershell`` walk that maps a command string to the same
three-level :class:`~aelix_coding_agent.builtin.bash_classifier.Verdict`. The
bash classifier's DENY is folded in as a FLOOR at the dispatch site
(``permission.py::_auto_classify_bash``), so nothing here can lower a verdict
that already blocks.

Why a grammar and not a tokenizer, measured against ``tree_sitter_powershell``
0.26.4: ``[System.Diagnostics.Process]::Start('calc')`` parses cleanly with
ZERO ``command`` nodes. A tokenizer scanning for a leading verb-noun finds
nothing there and has nothing to fail safe ON; the AST hands us
``invokation_expression`` / ``type_literal`` to refuse. The same probe found
``$x`` alone: ``has_error=False``, zero commands — which is why :func:`fold`
has ASK, not ALLOW, as its identity.

Every rule below that exists because a probe said so carries the probe's shape
in its docstring. The probe is ``/tmp/probe_ps.py`` in the #204 worktree; the
shapes it printed are quoted where they decide something.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aelix_coding_agent.builtin.bash_classifier import (
    _READ_ONLY_SWITCHES,
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


class _GrammarUnavailable(Exception):
    """Raised internally when ``tree-sitter-powershell`` could not be loaded."""


def _load_parser() -> Any:
    """Build a tree-sitter ``Parser`` for PowerShell, or raise.

    Mirrors ``bash_classifier._load_parser`` exactly, including the two-stage
    try: an ImportError (no wheel for this platform) and an ABI mismatch are
    different failures with the same fail-safe answer.
    """

    try:
        import tree_sitter_powershell as tsps  # noqa: PLC0415 — optional C-ext, lazy
        from tree_sitter import Language, Parser  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — any import problem → fail safe
        raise _GrammarUnavailable(str(exc)) from exc
    try:
        return Parser(Language(tsps.language()))
    except Exception as exc:  # noqa: BLE001 — ABI / construction failure
        raise _GrammarUnavailable(str(exc)) from exc


_PS_PARSER: Any | None
try:
    _PS_PARSER = _load_parser()
except _GrammarUnavailable:
    _PS_PARSER = None


# Every dash codepoint PowerShell's own tokenizer accepts as a PARAMETER dash —
# ``-`` U+002D, ``–`` U+2013, ``—`` U+2014, ``―`` U+2015 (``CharTraits.cs:12-15``
# and ``:254-259``, consumed at ``tokenizer.cs:4745-4749``, v7.4.6). Measured:
# ``Remove-Item –Recurse –Force C:\`` yields three ``generic_token``s and NO
# ``command_parameter``, so every parameter rule is one codepoint from evasion
# unless the text is normalised before any node-type test.
_DASH_HOMOGLYPHS = str.maketrans({"–": "-", "—": "-", "―": "-"})

# Aliases, version-collapsed to the MORE DANGEROUS meaning where 5.1 and 7.x
# disagree. Extracted from ``BuiltInAliases`` in PowerShell v7.4.6 (146 entries)
# and tag ``SD/725290`` (Windows PowerShell 5.1, 151 entries) with the
# ``#if !UNIX`` / ``#if !CORECLR`` conditions preserved.
#
# The collapse that matters is ``sc``: under 5.1 it is ``Set-Content`` and
# writes a file, under PS7 it is ``sc.exe``, the service controller
# (``sc delete <svc>``). Both are dangerous, so it resolves to the write cmdlet
# and can never reach ALLOW under either reading. ``curl``/``wget`` are aliases
# only on 5.1 — on PS7 they are the real binaries — but both meanings are ASK,
# so the collapse costs nothing. ``date`` is NOT an alias in either tree
# (grepped both) and is deliberately absent.
_PS_ALIASES: dict[str, str] = {
    "gci": "get-childitem",
    "dir": "get-childitem",
    "ls": "get-childitem",
    "gc": "get-content",
    "cat": "get-content",
    "type": "get-content",
    "gi": "get-item",
    "gp": "get-itemproperty",
    "gl": "get-location",
    "pwd": "get-location",
    "gcm": "get-command",
    "gm": "get-member",
    "gal": "get-alias",
    "gv": "get-variable",
    "ps": "get-process",
    "select": "select-object",
    "sls": "select-string",
    "where": "where-object",
    "?": "where-object",
    "sort": "sort-object",
    "group": "group-object",
    "measure": "measure-object",
    "compare": "compare-object",
    "diff": "compare-object",
    "foreach": "foreach-object",
    "%": "foreach-object",
    "echo": "write-output",
    "write": "write-output",
    "fl": "format-list",
    "ft": "format-table",
    "fw": "format-wide",
    "fc": "format-custom",  # Format-Custom, NOT cmd's file-compare
    "iex": "invoke-expression",
    "iwr": "invoke-webrequest",
    "irm": "invoke-restmethod",
    "curl": "invoke-webrequest",
    "wget": "invoke-webrequest",
    "rm": "remove-item",
    "rmdir": "remove-item",
    "del": "remove-item",
    "erase": "remove-item",
    "rd": "remove-item",
    "ri": "remove-item",
    "mv": "move-item",
    "move": "move-item",
    "mi": "move-item",
    "cp": "copy-item",
    "copy": "copy-item",
    "cpi": "copy-item",
    "ren": "rename-item",
    "rni": "rename-item",
    "ni": "new-item",
    "si": "set-item",
    "sp": "set-itemproperty",
    "ac": "add-content",
    "sc": "set-content",
    "kill": "stop-process",
    "spps": "stop-process",
    "saps": "start-process",
    "start": "start-process",
    "tee": "tee-object",
    "ii": "invoke-item",
    "sl": "set-location",
    "cd": "set-location",
    "chdir": "set-location",
    "sleep": "start-sleep",
    "md": "mkdir",
    "cls": "clear-host",
    "clear": "clear-host",
    "h": "get-history",
    "history": "get-history",
}

# The ALLOW tier. Matched AFTER alias resolution, and there is no ``Get-*``
# wildcard: every entry is a name someone read. ``get-help`` is absent because
# ``-Online`` launches a browser and a fresh install prompts to download help;
# ``start-sleep`` because ``Start-Sleep 999999`` hangs the AUTO session, the
# identical hazard that bars ``Get-Content -Wait``; ``get-variable`` because
# ``Get-Variable *`` dumps every session variable, the exfil argument that
# excluded ``Get-Clipboard``; ``set-location`` because it changes the shell
# state of every later command in the session.
_PS_ALLOW = frozenset(
    {
        "get-childitem",
        "get-item",
        "get-content",
        "get-location",
        "get-date",
        "get-command",
        "get-member",
        "get-alias",
        "get-process",
        "get-service",
        "get-psdrive",
        "get-module",
        "get-random",
        "get-acl",
        "get-itemproperty",
        "get-itempropertyvalue",
        "get-timezone",
        "get-filehash",
        "get-culture",
        "get-verb",
        "select-object",
        "select-string",
        "where-object",
        "sort-object",
        "group-object",
        "measure-object",
        "compare-object",
        "foreach-object",
        "format-list",
        "format-table",
        "format-wide",
        "format-custom",
        "out-string",
        "out-host",
        "write-output",
        "write-host",
        "test-path",
        "resolve-path",
        "convert-path",
        "split-path",
        "join-path",
        "convertto-json",
        "convertfrom-json",
        "convertto-csv",
        "convertfrom-csv",
        "import-csv",
    }
)

# Explicit ASK, never an implicit fallthrough — an unknown name ASKs anyway,
# but naming these makes the intent visible and testable, the way
# ``_ALWAYS_ASK_COMMANDS`` does on the bash side.
_PS_ASK = frozenset(
    {
        "stop-process",
        "stop-service",
        "start-service",
        "restart-service",
        "start-process",
        "invoke-item",
        "invoke-command",
        "enter-pssession",
        "new-pssession",
        "add-type",
        "new-object",
        "import-module",
        "register-scheduledtask",
        "new-service",
        "set-service",
        "get-credential",
        "get-clipboard",
        "set-clipboard",
        "get-history",
        "get-wmiobject",
        "get-ciminstance",
        "invoke-wmimethod",
        "get-help",
        "update-help",
        "set-psreadlineoption",
        "get-variable",
        "start-sleep",
        "set-location",
        "get-error",
        "get-pscallstack",
        "get-computerinfo",
        "mkdir",
        "clear-host",
    }
)

# Categorical DENY, position-independent. ``invoke-expression`` executes an
# arbitrary STRING: the payload is a ``string`` node, never a subtree, so there
# is nothing to descend into and ASK would be an unbounded prompt. The storage
# row is the ``mkfs``/``fdisk`` transposition.
_PS_DENY = frozenset(
    {
        "invoke-expression",
        "set-executionpolicy",
        "format-volume",
        "clear-disk",
        "initialize-disk",
        "remove-partition",
        "set-partition",
        "clear-recyclebin",
    }
)

# ``curl … | sh`` transposed: an interpreter at a non-first pipeline stage runs
# whatever was piped into it. ``invoke-expression`` is repeated here on purpose
# so the intent survives if the categorical row above is ever narrowed.
_PS_PIPE_INTO_DENY = frozenset(
    {
        "invoke-expression",
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "cmd",
        "powershell",
        "pwsh",
        "wsl",
    }
)

# Cmdlets whose TARGET decides the verdict: protected prefix → DENY, else ASK.
#
# ``invoke-webrequest``/``invoke-restmethod`` are here for their ``-OutFile``:
# measured on this branch before they were, ``iwr http://x -OutFile
# C:\Windows\y`` was ASK while ``Get-Content x | Out-File -FilePath
# C:\Windows\z`` and ``New-Item -Path C:\Windows\z`` were DENY. Fetch-and-write
# into ``C:\Windows`` is the most consequential shape this classifier can see
# and it was the one write to a protected prefix that escaped the rule.
# ``-OutFile`` is in :data:`_PS_PARAM_CAPABLE`, so these names could never reach
# ALLOW — but nothing raised them to DENY either.
_PS_WRITE_CMDLETS = frozenset(
    {
        "invoke-webrequest",
        "invoke-restmethod",
        "out-file",
        "set-content",
        "add-content",
        "clear-content",
        "clear-item",
        "copy-item",
        "move-item",
        "new-item",
        "rename-item",
        "set-item",
        "set-itemproperty",
        "new-itemproperty",
        "remove-itemproperty",
        "tee-object",
        "export-csv",
        "export-clixml",
        "set-acl",
    }
)

# They rebind the names the tables above are about to trust, so their presence
# ANYWHERE taints the whole command (#204 §C.2).
_PS_ALIAS_REBINDERS = frozenset({"set-alias", "new-alias", "sal", "nal"})

# Reflective execution: no command node exists, so only the node types refuse.
_REFLECTIVE_TYPES = ("process", "assembly", "webclient")
_REFLECTIVE_MEMBERS = frozenset(
    {"start", "load", "run", "invoke", "loadwithpartialname"}
)

_TAKEOWN_DENY_SWITCHES = frozenset({"/f"})
_ICACLS_DENY_SWITCHES = frozenset(
    {
        "/grant",
        "/grant:r",
        "/deny",
        "/setowner",
        "/reset",
        "/remove",
        "/inheritance:r",
        "/setintegritylevel",
    }
)

# ONE shared read-only parameter allowlist, prefix-matched, instead of a gate
# per cmdlet. PowerShell resolves any UNAMBIGUOUS case-insensitive prefix
# (``MergedCommandParameterMetadata.cs:469``, ambiguity check ``:508``, v7.4.6)
# and this classifier cannot prove unambiguity, so a parameter is accepted only
# if it prefixes something read-only AND prefixes nothing capability-bearing.
#
# ``first`` and ``last`` are deliberately NOT here even though they are
# read-only on ``Select-Object``: ``last`` makes ``-la`` an accepted prefix, and
# ``ls -la`` must stay ASK — it is the case
# ``test_permission_shell_competence.py::test_windows_shell_forces_ask_instead_of_auto_allow``
# drives. ``Select-Object -First 10`` therefore ASKs, which is the fail-safe
# direction and the cheaper mistake.
_PS_PARAM_ALLOW = frozenset(
    {
        "path",
        "literalpath",
        "filter",
        "include",
        "exclude",
        "recurse",
        "force",
        "name",
        "depth",
        "skip",
        "totalcount",
        "head",
        "tail",
        "raw",
        "encoding",
        "delimiter",
        "pattern",
        "simplematch",
        "casesensitive",
        "notmatch",
        "allmatches",
        "context",
        "list",
        "algorithm",
        "property",
        "expandproperty",
        "unique",
        "descending",
        "stable",
        "member",
        "group",
        "noelement",
        "format",
        "inputobject",
        "nonewline",
        "width",
        "autosize",
        "wrap",
        "errorvariable",
        "warningvariable",
        "verbose",
        "debug",
        "erroraction",
        "warningaction",
        "informationaction",
        "outbuffer",
        "pipelinevariable",
        "stream",
        "resolve",
        "relativebasepath",
        "leaf",
        "parent",
        "qualifier",
        "noqualifier",
        "isabsolute",
        "childpath",
        "additionalchildpath",
        "compress",
    }
)

# Anything that reaches outside the read, checked as a PREFIX so ``-o`` is
# refused for prefixing both ``outbuffer`` (allowed) and ``outvariable``
# (capable) at once — ambiguity is refused, not resolved.
_PS_PARAM_CAPABLE = frozenset(
    {
        "outvariable",
        "outfile",
        "append",
        "wait",
        "computername",
        "session",
        "asjob",
        "credential",
        "online",
        "membername",
        "argumentlist",
        "scriptblock",
        "encodedcommand",
        "encodedarguments",
        "executionpolicy",
        "command",
        "file",
        "confirm",
        "whatif",
        "dynamicparameter",
        "usenewenvironment",
        "verb",
        "passthru",
        "destination",
        "value",
    }
)

# ``-EncodedCommand`` is base64 and unanalysable. The exclusion is required,
# not defensive: ``about_pwsh`` documents ``-e`` as EncodedCommand while
# ``-ex``/``-ep`` are ExecutionPolicy, so a naive ``-e``-prefix rule
# over-matches ``pwsh -ex Bypass``.
_ENCODED_PARAMS = ("encodedcommand", "encodedarguments")
_NOT_ENCODED = frozenset({"ex", "ep"})

# Which parameter names name the thing being written to / deleted. ``outfile``
# is ``Invoke-WebRequest``'s spelling of the same thing.
_TARGET_PARAMS = ("path", "filepath", "literalpath", "destination", "outfile")

# Wrapper node types the grammar interposes between a command element and the
# thing it actually is: ``array_literal_expression > unary_expression >
# script_block_expression`` for ``{ … }``, measured. Unwrapping stops at these.
_UNWRAP_STOP = frozenset(
    {
        "script_block_expression",
        "string_literal",
        "variable",
        "sub_expression",
        "array_expression",
        "parenthesized_expression",
        "invokation_expression",
        "member_access",
        "type_literal",
        "cast_expression",
        "hash_literal_expression",
        "generic_token",
        "command_parameter",
        "command",
        "integer_literal",
    }
)

# Walked as FRESH statements at ``stage_index=0`` — the analogue of the bash
# classifier's ``_contains_executable_node`` re-walk.
_FRESH_STATEMENT = frozenset(
    {
        "script_block_expression",
        "sub_expression",
        "array_expression",
        "parenthesized_expression",
    }
)

# Node types whose structure this walk understands; anything else that wraps a
# command is seeded ASK rather than ALLOW (the bash classifier's WP-0 #6 rule,
# transposed) so a grammar bump introducing a new command-wrapping node cannot
# fail open.
_KNOWN_CONTAINERS = frozenset(
    {
        "program",
        "statement_list",
        "pipeline",
        "pipeline_chain",
        "pipeline_chain_tail",
        "script_block",
        "script_block_body",
        "empty_statement",
        "comment",
        "command_elements",
        "command_argument_sep",
        "redirected_file_name",
        "file_redirection_operator",
        "command_name",
        "command_name_expr",
        "path_command_name",
        "command_invokation_operator",
        "argument_list",
        "argument_expression_list",
        "argument_expression",
        "member_name",
        "simple_name",
        "type_spec",
        "type_name",
        "type_identifier",
        "comparison_operator",
        "assignement_operator",
        "left_assignment_expression",
    }
)


@dataclass
class _Element:
    """One argument of a ``command``, read as either a parameter or a value."""

    node: Any
    is_parameter: bool
    name: str
    literal: str | None
    raw: str
    colon: bool = False
    colon_value: str | None = None
    is_colon_value: bool = False


def _text(node: Any) -> str:
    return node.text.decode(errors="replace")


def _ps_arg_text(node: Any) -> str:
    """Node text with the two evasions the grammar does not undo, then casefold.

    Backticks first (measured: ``Rem`ove-Item x`` parses with
    ``has_error=False`` and a ``command_name`` of ``Rem`ove-Item``), then every
    dash codepoint to ASCII.
    """

    return _text(node).replace("`", "").translate(_DASH_HOMOGLYPHS).casefold()


def _ps_literal(node: Any) -> str | None:
    """The static string a value node denotes, or ``None`` if it is dynamic.

    Never casefolded: a path's case is checked by the target predicates, which
    fold it themselves, and a positional handed to
    :func:`~aelix_coding_agent.builtin.bash_classifier.arguments_are_read_only`
    must keep its case because GNU short options are case-sensitive.
    """

    inner = _unwrap(node)
    kind = inner.type
    if kind in ("generic_token", "command_name", "integer_literal"):
        return _text(inner).replace("`", "")
    if kind == "string_literal":
        named = [child for child in inner.children if child.is_named]
        if not named:
            return None
        body = named[0]
        text = _text(body)
        if body.type == "verbatim_string_characters":
            if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
                # ``'it''s'`` — the doubled quote is the escape, measured as one
                # ``verbatim_string_characters`` node.
                return text[1:-1].replace("''", "'")
            return None  # a here-string body: no single literal to check
        if body.type == "expandable_string_literal":
            if any(child.is_named for child in body.children):
                return None  # contains a ``variable`` / subexpression → dynamic
            if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
                return text[1:-1]
        return None
    return None


def _unwrap(node: Any) -> Any:
    """Descend through single-named-child wrappers to the node that decides."""

    current = node
    while current.type not in _UNWRAP_STOP:
        named = [child for child in current.children if child.is_named]
        if len(named) != 1:
            return current
        current = named[0]
    return current


def _ps_basename(raw: str) -> str:
    """A command name as WRITTEN, reduced to a comparable key.

    Strip backticks, strip the quotes a quoted invocation keeps (measured:
    ``& 'Remove-Item' x`` yields a ``command_name_expr`` whose text is
    ``'Remove-Item'`` WITH the quotes), then strip the path and ``.exe`` via
    ``shell_basename``.

    Homoglyphs are NOT folded away here: ``Ｇet-Date`` (U+FF27) casefolds to
    ``ｇet-date``, matches no table and ASKs, which is the honest answer for a
    name PowerShell itself would not resolve either.
    """

    from aelix_coding_agent.tools.bash import shell_basename  # noqa: PLC0415

    text = raw.replace("`", "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return shell_basename(text)


def _ps_normalize(raw: str) -> str:
    """:func:`_ps_basename`, then resolved through :data:`_PS_ALIASES`."""

    name = _ps_basename(raw)
    return _PS_ALIASES.get(name, name)


def _staged_children(chain: Any) -> list[tuple[Any, int]]:
    """Each named child of a ``pipeline_chain`` with its 0-based stage (F14).

    Measured, and the first draft of this rule was exactly inverted::

        'iwr http://x | iex' -> pipeline_chain[command, |(ANON), command]
        'a && b || c'        -> pipeline[chain, chain_tail('&&'), chain, …]

    ``|`` is an ANONYMOUS token child of ``pipeline_chain``; ``pipeline_chain_tail``
    wraps ONLY ``&&`` and ``||``. So the stage index counts ``|`` siblings, and
    a new chain starts at 0 — ``a && iex 'x'`` is sequencing, not fetch-and-run,
    and every "interpreter at stage > 0" rule is dead code if this is backwards.
    """

    staged: list[tuple[Any, int]] = []
    index = 0
    for child in chain.children:
        if not child.is_named:
            if child.type == "|":
                index += 1
            continue
        staged.append((child, index))
    return staged


def _contains(node: Any, types: frozenset[str]) -> bool:
    if node.type in types:
        return True
    return any(_contains(child, types) for child in node.children)


_EXECUTABLE_TYPES = frozenset({"command", "invokation_expression"})
_COMMAND_TYPES = frozenset({"command"})


def _command_name_node(command: Any) -> Any | None:
    for child in command.children:
        if child.type in ("command_name", "command_name_expr"):
            return child
    return None


def _elements(command: Any) -> tuple[list[_Element], list[Any]]:
    """Split a ``command``'s ``command_elements`` into arguments and redirects.

    A token is a PARAMETER if it is a ``command_parameter`` node **or** a
    ``generic_token`` whose dash-normalised text starts with ``-`` — the second
    half is F1: with U+2013 the grammar produces no ``command_parameter`` at
    all.

    A ``-Switch:<value>`` binding is recorded on the PARAMETER: measured,
    ``-Recurse:$true`` is ``command_parameter`` + ``command_argument_sep(':')``
    + ``array_literal_expression > unary_expression > variable``, i.e. the value
    is a separate element whose literal is ``None``. Carrying its raw text is
    what lets :func:`_switch_is_on` tell the DISABLING spelling from the
    ENABLING one, and marking it as the parameter's value keeps it out of
    :func:`_positionals`, where it would otherwise be read as a path.
    """

    elements: list[_Element] = []
    redirections: list[Any] = []
    container = None
    for child in command.children:
        if child.type == "command_elements":
            container = child
    if container is None:
        return elements, redirections
    for child in container.children:
        kind = child.type
        if kind == "command_argument_sep":
            # ``-Path:C:\x`` — measured, the colon is its own separator node.
            if ":" in _text(child) and elements and elements[-1].is_parameter:
                elements[-1].colon = True
            continue
        if kind == "redirection":
            redirections.append(child)
            continue
        if kind == "command_parameter":
            elements.append(
                _Element(
                    node=child,
                    is_parameter=True,
                    name=_ps_arg_text(child).lstrip("-"),
                    literal=None,
                    raw=_text(child),
                )
            )
            continue
        normalised = _ps_arg_text(child)
        if kind == "generic_token" and normalised.startswith("-"):
            elements.append(
                _Element(
                    node=child,
                    is_parameter=True,
                    name=normalised.lstrip("-"),
                    literal=None,
                    raw=_text(child),
                )
            )
            continue
        elements.append(
            _Element(
                node=child,
                is_parameter=False,
                name="",
                literal=_ps_literal(child),
                raw=_text(child),
            )
        )
    for index, element in enumerate(elements[:-1]):
        if element.colon and not elements[index + 1].is_parameter:
            element.colon_value = elements[index + 1].raw
            elements[index + 1].is_colon_value = True
    return elements, redirections


def _param_ok(param: str) -> bool:
    """Accepted only if it prefixes something read-only and nothing capable."""

    if not param:
        return False
    return any(a.startswith(param) for a in _PS_PARAM_ALLOW) and not any(
        d.startswith(param) for d in _PS_PARAM_CAPABLE
    )


def _positionals(elements: list[_Element]) -> list[_Element]:
    return [
        element
        for element in elements
        if not element.is_parameter and not element.is_colon_value
    ]


# The values that DISABLE a ``-Switch:<value>`` binding. Everything else —
# ``$true``, ``1``, a variable this classifier cannot resolve — counts the
# switch as PRESENT. Measured on this branch before that asymmetry existed:
# ``Remove-Item -Recurse:$true -Force:$true C:\`` was ASK while
# ``Remove-Item -Recurse -Force C:\`` was DENY, because the colon form was
# skipped wholesale to keep ``-Recurse:$false`` out of the DENY branch. The
# enabling spelling deletes the drive, so "colon → not present" was the
# fail-OPEN half of a rule written for the fail-safe half.
_SWITCH_OFF_LITERALS = frozenset({"$false", "false", "0", "$null"})


def _switch_is_on(element: _Element) -> bool:
    """Whether a switch parameter is ENABLED, given its ``:``-attached value."""

    if not element.colon:
        return True
    return (element.colon_value or "").strip().casefold() not in _SWITCH_OFF_LITERALS


def _target(elements: list[_Element]) -> str | None:
    """The path a write / delete acts on: a target parameter's value, else the
    first positional. ``None`` when there is none, which is NOT "harmless"."""

    for index, element in enumerate(elements):
        if element.is_parameter and any(
            name.startswith(element.name) for name in _TARGET_PARAMS
        ):
            for candidate in elements[index + 1 :]:
                if not candidate.is_parameter:
                    return candidate.literal
            return None
    positionals = _positionals(elements)
    return positionals[0].literal if positionals else None


def _write_targets(name: str, elements: list[_Element]) -> list[str | None]:
    """Every path a write cmdlet can land on, not just the first.

    The spec's single-target rule reads the first positional, which is the
    SOURCE for the three two-argument cmdlets — measured,
    ``Copy-Item a C:\\Windows\\b`` came out ASK because ``a`` was all it looked
    at. Their destination is the second positional, so it is checked too. The
    one-argument cmdlets are unchanged: ``Set-Content notes.txt /etc/passwd``
    must not be DENYed for a CONTENT string that happens to look like a path.
    """

    targets = [_target(elements)]
    if name in ("copy-item", "move-item", "rename-item"):
        positionals = _positionals(elements)
        if len(positionals) > 1:
            targets.append(positionals[1].literal)
    return targets


def _is_script_block(element: _Element) -> bool:
    return _unwrap(element.node).type == "script_block_expression"


def _redirection_verdict(redirection: Any) -> Verdict:
    """Any redirection drops the statement out of the ALLOW tier (F5).

    Measured: ``Get-Date > C:\\Windows\\x`` puts ``redirection`` INSIDE
    ``command_elements``, so the name table would otherwise never see it. One
    global rule instead of a per-row "no redirect" clause, which is what makes
    it cover every future ALLOW-tier addition too.
    """

    for child in redirection.children:
        if child.type != "redirected_file_name":
            continue
        for value in child.children:
            if value.type in ("command_argument_sep",) or not value.is_named:
                continue
            target = _ps_literal(value)
            return Verdict.DENY if protected_write_target(target) else Verdict.ASK
    return Verdict.ASK


def _reflective_verdict(node: Any) -> Verdict:
    """``[Type]::Member(…)`` — DENY the reflective-execution shapes, else ASK."""

    type_text = ""
    member = ""
    for child in node.children:
        if child.type == "type_literal":
            type_text = _ps_arg_text(child).strip("[]")
        elif child.type == "member_name":
            member = _ps_arg_text(child)
    if member in _REFLECTIVE_MEMBERS and type_text.endswith(_REFLECTIVE_TYPES):
        return Verdict.DENY
    return Verdict.ASK


def _allow_verdict(name: str, written: str, elements: list[_Element]) -> Verdict:
    """The ALLOW tier's gates, all of them, before the verdict is granted.

    "Never ALLOW an argument we did not read" is the same doctrine the bash
    side states; here it costs six gates because a cmdlet's arguments can be a
    parameter, a positional, a script block, a provider drive or a UNC share.
    """

    if name in ("foreach-object", "where-object"):
        # Measured: ``Get-ChildItem | ForEach-Object -MemberName Delete`` is
        # ``command_parameter`` + ``generic_token`` — two ALLOW names, no script
        # block, and it deletes every file. So these two are ALLOW only when
        # EVERY argument is a script block, whose body is then folded by the
        # re-walk in :func:`_walk`.
        if not elements or not all(
            not element.is_parameter and _is_script_block(element)
            for element in elements
        ):
            return Verdict.ASK
        return Verdict.ALLOW
    for element in elements:
        if element.is_parameter:
            if not _param_ok(element.name):
                return Verdict.ASK
            continue
        if element.literal is None:
            return Verdict.ASK
        if sensitive_read_target(element.literal):
            return Verdict.ASK
    if written in _READ_ONLY_SWITCHES:
        # ``sort``/``date``/``hostname`` are BOTH a cmdlet alias and a real
        # binary depending on version and platform, so the ALLOW has to survive
        # both readings (F4). ``Dialect.POWERSHELL`` refuses a write switch of
        # POSIX *or* cmd while reading a ``/``-leading token as a path, which is
        # what makes ``sort /etc/hosts`` ALLOW and ``sort in.txt /o out.txt``
        # ASK under the same rule.
        tokens: list[str | None] = [
            element.literal if element.literal is not None else element.raw
            for element in elements
        ]
        if not arguments_are_read_only(written, tokens, Dialect.POWERSHELL):
            return Verdict.ASK
    return Verdict.ALLOW


def _remove_item_verdict(elements: list[_Element]) -> Verdict:
    """``Remove-Item`` — DENY only the recursive-force-on-a-root shape.

    Mirrors ``_classify_recursive_mutator``, which is the shape this repo
    already chose for ``chmod -R``. NOT "any ``Remove-Item``":
    ``Remove-Item build\\out.js`` is an ordinary edit, and DENYing it would make
    AUTO unusable on Windows and train click-through.

    ``-Recurse:$false`` DISABLES the switch and must not count as present;
    ``-Recurse:$true`` ENABLES it and deletes the drive. Both go through
    :func:`_switch_is_on`, which reads the bound value instead of refusing every
    colon form — the earlier "colon → not present" rule kept the first case out
    of the DENY branch by keeping the second one out too.
    """

    recurse = force = False
    for element in elements:
        if not element.is_parameter or not _switch_is_on(element):
            continue
        if "recurse".startswith(element.name):
            recurse = True
        if "force".startswith(element.name):
            force = True
    if recurse and force and is_root_or_protected_target(_target(elements)):
        return Verdict.DENY
    return Verdict.ASK


def _acl_verdict(name: str, elements: list[_Element]) -> Verdict:
    """``takeown``/``icacls`` — DENY only when they retarget a protected tree."""

    switches = _TAKEOWN_DENY_SWITCHES if name == "takeown" else _ICACLS_DENY_SWITCHES
    values = [e.literal for e in elements if not e.is_parameter and e.literal is not None]
    lowered = [v.casefold() for v in values]
    if not any(v in switches or v.split(":", 1)[0] in switches for v in lowered):
        return Verdict.ASK
    targets = [v for v in values if not v.startswith("/")]
    if any(is_root_or_protected_target(v) for v in targets):
        return Verdict.DENY
    return Verdict.ASK


def _name_verdict(
    name: str, written: str, elements: list[_Element], stage_index: int
) -> Verdict:
    if name in _PS_DENY:
        return Verdict.DENY
    if stage_index > 0 and name in _PS_PIPE_INTO_DENY:
        return Verdict.DENY
    if name in ("powershell", "pwsh") and any(
        element.is_parameter
        and element.name not in _NOT_ENCODED
        and any(param.startswith(element.name) for param in _ENCODED_PARAMS)
        for element in elements
    ):
        return Verdict.DENY
    if name == "remove-item":
        return _remove_item_verdict(elements)
    if name in ("takeown", "icacls"):
        return _acl_verdict(name, elements)
    if name in _PS_WRITE_CMDLETS:
        if any(protected_write_target(t) for t in _write_targets(name, elements)):
            return Verdict.DENY
        return Verdict.ASK
    if name in _PS_ASK:
        return Verdict.ASK
    if name in _PS_ALLOW:
        return _allow_verdict(name, written, elements)
    return Verdict.ASK


def _walk_command(command: Any, stage_index: int) -> Verdict:
    name_node = _command_name_node(command)
    if name_node is None:
        return Verdict.ASK
    raw = _ps_literal(name_node) if name_node.type == "command_name_expr" else _text(name_node)
    if raw is None or not raw.strip():
        # ``& $x`` / ``& ('r'+'m')`` — the name is decided at runtime.
        return Verdict.ASK
    written = _ps_basename(raw)
    if not written:
        return Verdict.ASK
    # BOTH names are carried: the tiers key on the alias-resolved one, while the
    # switch-table gate keys on the name as WRITTEN, because ``sort`` is the
    # GNU binary as often as it is ``Sort-Object`` (F4) and ``sort-object`` has
    # no switch grammar.
    name = _PS_ALIASES.get(written, written)
    elements, redirections = _elements(command)
    parts = [_name_verdict(name, written, elements, stage_index)]
    parts.extend(_redirection_verdict(node) for node in redirections)
    # A script block, ``$( … )`` or ``( … )`` handed to an ALLOW name carries
    # its own statements; walk each as a fresh statement so an embedded DENY
    # bubbles up through the fold, exactly as the bash classifier re-walks a
    # command substitution passed as an argument.
    parts.extend(_walk(element.node, stage_index=0) for element in elements)
    return fold(parts)


def _walk(node: Any, *, stage_index: int) -> Verdict:
    """Recursive worst-of traversal; ``stage_index`` is the pipeline position."""

    kind = node.type
    if kind.endswith("_statement") and kind != "empty_statement":
        # ``function``/``if``/``foreach`` — the bash classifier's ``_CONTROL_FLOW``
        # rule transposed. A ``function_statement`` additionally rebinds a name
        # the tables trust, which is the same reason under a different heading.
        return Verdict.ASK
    if kind == "stop_parsing":
        return Verdict.ASK  # ``--%`` hands the rest of the line to no parser
    if kind == "invokation_expression":
        return _reflective_verdict(node)
    if kind in ("cast_expression", "type_literal"):
        return Verdict.ASK
    if kind == "redirection":
        return _redirection_verdict(node)
    if kind == "command":
        return _walk_command(node, stage_index)
    if kind == "pipeline_chain":
        return fold(
            [Verdict.ALLOW]
            + [_walk(child, stage_index=index) for child, index in _staged_children(node)]
        )
    if kind in _FRESH_STATEMENT:
        return fold(
            [Verdict.ALLOW]
            + [_walk(child, stage_index=0) for child in node.children if child.is_named]
        )
    if node.is_named and node.children:
        recognised = (
            kind in _KNOWN_CONTAINERS
            or kind.endswith("_expression")
            or kind.endswith("_literal")
            or kind.endswith("_characters")
        )
        seed = (
            Verdict.ALLOW
            if recognised or not _contains(node, _EXECUTABLE_TYPES)
            else Verdict.ASK
        )
        return fold(
            [seed]
            + [
                _walk(child, stage_index=stage_index)
                for child in node.children
                if child.is_named
            ]
        )
    return Verdict.ALLOW


def _rebinds_an_alias(node: Any) -> bool:
    if node.type == "command":
        name_node = _command_name_node(node)
        if (
            name_node is not None
            and name_node.type == "command_name"
            and _ps_normalize(_text(name_node)) in _PS_ALIAS_REBINDERS
        ):
            return True
    return any(_rebinds_an_alias(child) for child in node.children)


def classify_powershell(command: str) -> Verdict:
    """Classify ``command`` as PowerShell (fail-safe to ASK, never raises).

    ASK for an empty command, for a missing grammar, for ``has_error`` (the
    ADR-0158 doctrine — ``Get-ChildItem -Filter=*.txt`` parses to an ``ERROR``
    root and is upstream issue #46), and on any traversal failure.

    The ``ALLOW`` seed is deliberately not ``max``'s identity: a tree with zero
    ``command`` nodes floors at ASK, because ``$x`` and a here-string both parse
    clean with none and an ALLOW identity would auto-run every expression-only
    line.
    """

    if not command or not command.strip():
        return Verdict.ASK
    if _PS_PARSER is None:
        return Verdict.ASK
    try:
        root = _PS_PARSER.parse(command.encode()).root_node
        if root.has_error:
            return Verdict.ASK
        floor = Verdict.ALLOW if _contains(root, _COMMAND_TYPES) else Verdict.ASK
        if _rebinds_an_alias(root):
            floor = Verdict.ASK
        return fold([floor, _walk(root, stage_index=0)])
    except Exception:  # noqa: BLE001 — any traversal failure → fail safe
        return Verdict.ASK


def powershell_grammar_available() -> bool:
    """Whether ``tree-sitter-powershell`` loaded (the tables can be reached)."""

    return _PS_PARSER is not None


__all__ = ["classify_powershell", "powershell_grammar_available"]
