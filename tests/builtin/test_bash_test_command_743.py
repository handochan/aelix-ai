"""#743 — an arithmetic operand of ``[`` / ``[[`` that the shell evaluates as code asks, as ``test``'s does.

On b160b8f7 ``[ 1 -eq 'a[$(touch PWNED)]' ]`` and ``[[ 1 -eq 'a[$(touch PWNED)]' ]]``
were ALLOW while ``test 1 -eq 'a[$(touch PWNED)]'`` asked. bash (``[[``) and
ksh93 (``[`` and ``[[``) evaluate an operand of ``-eq``, ``-ne``, ``-lt``,
``-le``, ``-gt`` or ``-ge`` as an arithmetic expression, array subscripts
included, so the ``$(…)`` in the subscript runs. tree-sitter makes ``[ … ]`` a
``test_command`` like ``[[ … ]]``, and that node's check asked only about
expansions and assignments, never about a static operand; the ``test``
command's check, which asks about one, is reached only through ``test``.

Every row was measured with ``<shell> -c`` in an empty scratch directory: macOS
bash 3.2.57, zsh 5.9, dash and ksh93u+ 2012-08-01, and Linux (docker
``python:3.12-slim``) bash 5.2.37, zsh 5.9, dash and ksh 93u+m/1.0.10
(``.omc/probes/743/impl/``), and in fix round 2 every row again in those
shells and in Linux mksh 59c, which is in ``_CLASSIFIABLE_SHELLS`` too
(``.omc/probes/743/fix-r2/``). The comment on a row names the shells that ran
the ``touch``; zsh, dash and Linux ksh 93u+m ran none.

Verdicts are compared with ``==``, not ``is``: ``test_bash_classifier.py``
reloads the classifier module last, which rebinds ``Verdict``.
"""

from __future__ import annotations

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
_OPERATORS = ("-eq", "-ne", "-lt", "-le", "-gt", "-ge")

# Each ran ``touch PWNED`` in the shells named, and was ALLOW on b160b8f7.
_RUNS_CODE = (
    # macOS ksh93u+ and mksh, either side of each operator.
    *(f"[ 1 {op} {_P} ]" for op in _OPERATORS),
    *(f"[ {_P} {op} 1 ]" for op in _OPERATORS),
    # bash 3.2, bash 5.2, macOS ksh93u+ and mksh.
    *(f"[[ 1 {op} {_P} ]]" for op in _OPERATORS),
    *(f"[[ {_P} {op} 1 ]]" for op in _OPERATORS),
    "[[ 1 -eq '1+a[$(touch PWNED)]' ]]",
    "[[ 1 -eq $'a[$(touch PWNED)]' ]]",
    "[[ 1 -eq 2 || 1 -eq 'a[$(touch PWNED)]' ]]",
    "[[ ! 1 -eq 'a[$(touch PWNED)]' ]]",
    "[[ ( 1 -eq 'a[$(touch PWNED)]' ) ]]",
    "[[ $((1)) -eq 'a[$(touch PWNED)]' ]]",
    # bash 3.2, macOS ksh93u+ and mksh (not Linux bash 5.2).
    '[[ 1 -eq "a[\\$(touch PWNED)]" ]]',
    # macOS ksh93u+ and mksh.
    "[ 1 -eq '1+a[$(touch PWNED)]' ]",
    "[ 1 -eq $'a[$(touch PWNED)]' ]",
    "[ ! 1 -eq 'a[$(touch PWNED)]' ]",
    # tree-sitter groups this ``((x = x) -a 1) -eq …``; the shells read ``-eq``'s
    # operands as ``1`` and the subscript.
    "[ x = x -a 1 -eq 'a[$(touch PWNED)]' ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _RUNS_CODE)
def test_an_arithmetic_operand_that_runs_code_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# A glob beside an arithmetic operator expands to file names. ALLOW on
# b160b8f7, ``test`` included. Fix round 2 measured each in macOS ksh93u+ and
# mksh, which ran it in one of two directories: with a lone file named
# ``a[$(id>PWNED)]`` the first, second, fourth and sixth, with files ``+1``,
# ``-eq`` and ``a[$(touch PWNED)]`` the second, third, fifth and sixth. No
# other shell ran any. ``[[`` does not glob (no shell ran it).
_GLOBS = (
    "[ 1 -eq * ]",
    "[ 1 -eq a* ]",
    "[ * -gt 1 ]",
    "test 1 -eq *",
    "test * -gt 1",
    "test a* -lt 1",
)


@pytest.mark.parametrize("command", _GLOBS)
def test_a_glob_beside_an_arithmetic_operator_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


# ALLOW on b160b8f7 and still ALLOW: no shell measured ran anything for the
# ``a[$(…)]`` rows, and the rest are the comparisons people write.
_STAYS_ALLOW = (
    '[ "$a" = "$b" ]',
    "[[ -f x ]]",
    "[ 1 -eq 2 ]",
    "[[ 10 -ge 9 ]]",
    "[ $# -eq 0 ]",
    '[ "$#" -gt 0 ]',
    "[[ ${#a[@]} -ne 0 ]]",
    '[ "${#x}" -lt 3 ]',
    "[ $((1+1)) -eq 2 ]",
    '[ "$?" -ne 0 ]',
    "[ $? -eq 0 ] && echo ok",
    "[ 0 -lt 1 -a 2 -gt 1 ]",
    "[ 1 -eq -1 ]",
    "[ -f a ] && [ 1 -le 2 ]",
    "[[ x =~ 'a[$(touch PWNED)]' ]]",
    "[[ 'a[$(touch PWNED)]' =~ x ]]",
    "[[ -t 'a[$(touch PWNED)]' ]]",
    "[ -t 'a[$(touch PWNED)]' ]",
    "[[ x == * ]]",  # ``[[`` does not glob; no shell ran it with files planted
    "[[ * == x ]]",
    "[[ x =~ * ]]",
    "[ $((1 + 2 * (3 - 1))) -eq 5 ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _STAYS_ALLOW)
def test_an_ordinary_comparison_stays_allow(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# Not a plain number or name: these ask, as the same operand of ``test``
# already did on b160b8f7 (ALLOW there for ``[``; no shell measured ran anything).
_LIKE_TEST = ("[ 1 -lt 'x[1]' ]", "[ 0x10 -eq 16 ]", '[ "" -eq 0 ]')


@pytest.mark.parametrize("command", _LIKE_TEST)
def test_an_operand_test_asks_about_asks_here_too(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


def test_the_words_are_read_in_source_order_whatever_the_grouping() -> None:
    source = b"[ ! 1 -ge x -a 2 -eq 3 ]"
    test = bc._PARSER.parse(source).root_node.children[0]
    assert test.type == "test_command"
    assert [w.text for w in bc._test_words(test)] == [
        b"[",
        b"!",
        b"1",
        b"-ge",
        b"x",
        b"-a",
        b"2",
        b"-eq",
        b"3",
        b"]",
    ]


@pytest.mark.parametrize(
    ("operand", "danger"),
    [
        ("1", False),
        ("' -1 '", False),
        ("x", True),  # fix round 2: every bare name
        ("_", True),
        ("BASH_REMATCH", True),
        ("'a[1]'", True),
        (_P, True),
        ("*", True),  # fix round 1: not a plain number
        ("'1,2'", True),
        ("0x10", True),
        ("$#", False),
        ('"$#"', False),
        ('"${#x}"', False),
        ("$((1+1))", False),
        ("$(( (1 + 2) * 3 % 4 ))", False),
        ("$((x+1))", True),  # fix round 2: a name inside asks as one outside does
        ("$((_))", True),
        ("$((x=1))", True),
        ("$(( '$(touch PWNED)' ))", True),
        ('$(( "a[\\$(touch PWNED)]" ))', True),
        ("$(( $# ))", True),  # any ``$`` inside asks
        # Fix round 2: only digits, blanks and operator characters, so a quote,
        # a ``[`` or a backslash alone asks as well (each harmless here).
        ("$(( '1' ))", True),
        ("$((1[0]))", True),
        ("$(( 1\\\n+1 ))", True),
        ("$((0x10))", True),
        ("$x", True),
        ('"$x"', True),
        ('"1$#"', True),
        ("$'1'", True),
    ],
)
def test_one_operand(operand: str, danger: bool) -> None:
    source = f"[[ 1 -eq {operand} ]]".encode()
    words = bc._test_words(bc._PARSER.parse(source).root_node.children[0])
    assert bc._arith_operand_danger(words[3]) is danger


# --- Fix round 1 ---
#
# Measured with ``<shell> -c`` on macOS bash 3.2.57, zsh 5.9, dash and ksh93u+
# and in Linux docker bash 5.2.37, zsh 5.9, dash and ksh 93u+m/1.0.10, in an
# empty directory and again with files ``+1``, ``-eq`` and
# ``a[$(touch PWNED)]`` planted (``.omc/probes/743/fix-r1/``). Each row was
# ALLOW on 61eda564 unless noted.

# bash fills ``_`` (the previous command's last argument) and ``BASH_REMATCH``
# (what ``=~`` matched) with text from the command itself, and evaluates it
# again as an arithmetic operand. bash 3.2 and 5.2 created PWNED for each of
# these; zsh, dash, ksh and mksh did not.
_NAMES_BASH_FILLS = (
    f"[[ {_P} =~ .* && BASH_REMATCH -eq 0 ]]",
    f"[[ {_P} =~ .* ]] && [[ BASH_REMATCH -eq 0 ]]",
    f"echo {_P}; [[ 1 -eq _ ]]",
    f"true {_P} && [[ _ -eq 0 ]]",
    f"ls -d {_P} 2>/dev/null; [[ 1 -eq _ ]]",
    f"echo {_P}; [[ 1 -eq $((_)) ]]",
    f"echo {_P}; [ 1 -eq $((_)) ]",
)
# No shell ran these; they asked since fix round 1 because the name is one bash
# fills, or the line matches with ``=~``.
_NAMES_ASK_ANYWAY = (
    "[[ _ -gt 0 ]]",
    f"echo {_P}; [ 1 -eq _ ]",
    f"echo {_P}; test 1 -eq _",
    f"[[ {_P} =~ .* ]] && [ BASH_REMATCH -eq 0 ]",
    "[[ x =~ y && n -eq 0 ]]",
    "[[ x =~ y ]] && test 1 -eq n",
)
# A variable the line sets itself: each was ASK on 61eda564 already (the
# setter asks), and bash created PWNED for each, macOS ksh93 and mksh for some.
_NAMES_THE_LINE_SETS = (
    f"x={_P}; [[ x -eq 0 ]]",
    f"read x <<< {_P}; [[ x -eq 0 ]]",
    f"read <<< {_P}; [[ REPLY -eq 0 ]]",
    f"printf -v x '%s' {_P}; [[ x -eq 0 ]]",
    f"declare x={_P}; [[ x -eq 0 ]]",
    f"f() {{ local x={_P}; [[ x -eq 0 ]]; }}; f",
    f"export x={_P}; [[ x -eq 0 ]]",
    f"for x in {_P}; do [[ x -eq 0 ]]; done",
    f"set -- -a {_P}; getopts a: o; [[ OPTARG -eq 0 ]]",
    f"mapfile x <<< {_P}; [[ x -eq 0 ]]",  # Linux bash 5.2 (3.2 has no mapfile)
)

# --- Fix round 2: every bare name asks ---
#
# The environment can hold the text too, and aelix's own ``load_dotenv`` puts
# a cloned repository's ``*_API_KEY`` names there. With ``FOO`` and
# ``EVIL_API_KEY`` set to ``a[$(touch PWNED)]`` in the environment, each of
# these created PWNED in the shells named (zsh, dash and Linux ksh 93u+m ran
# none); all were ALLOW on ce3cc695. That a bare name always asks is the lead's
# call in fix round 2, not the owner's, and the owner can loosen it.
_NAMES_THE_ENVIRONMENT_HOLDS = (
    # bash 3.2 and 5.2, macOS ksh93u+, mksh.
    "[[ FOO -eq 0 ]]",
    "[[ 1 -eq FOO ]]",
    "[[ EVIL_API_KEY -eq 0 ]]",
    "[[ 1 -eq $((FOO)) ]]",
    "[ 1 -eq $((FOO)) ]",
    "test 1 -eq $((FOO))",
    "[ 1 -eq $((EVIL_API_KEY)) ]",
    "[[ 1 -eq $((FOO+1)) ]]",
    # macOS ksh93u+ and mksh.
    "[ 1 -eq FOO ]",
    "test 1 -eq FOO",
    "[ 1 -eq EVIL_API_KEY ]",
)
# The cost: names the environment rarely or never holds ask too (ALLOW on
# ce3cc695; no shell measured ran anything with them unset).
_NAMES_NOW_ASK = (
    "[[ x -eq 0 ]]",
    "[ x -eq 0 ]",
    "test 1 -eq n",
    "[[ count -gt 3 ]]",
    "[ $((x+1)) -eq 2 ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(
    "command",
    _NAMES_BASH_FILLS
    + _NAMES_ASK_ANYWAY
    + _NAMES_THE_LINE_SETS
    + _NAMES_THE_ENVIRONMENT_HOLDS
    + _NAMES_NOW_ASK,
)
def test_a_bare_name_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# A word with an unquoted glob can expand into an operator and its operand.
# With the three files planted, macOS ksh93u+ created PWNED for each of these
# (``test 1 *`` with only ``-eq`` and ``a[$(touch PWNED)]`` planted, Codex's
# directory), and mksh for all but the ``-o`` pair and ``test 1 *`` (fix
# round 2); no other shell did, and none without the files.
_GLOBS_RUN = (
    "[ * ]",
    "[ ! * ]",
    "[ * = x ]",
    "[ 1 = 1 -a * ]",
    "[ 1 -o * ]",
    '[ "$x"* ]',
    "test *",
    "test ! *",
    "test 1 -o *",
    "test 1 *",
)
# No shell ran these; a glob anywhere asks (the cost of the rule).
_GLOBS_ASK_ANYWAY = ("[ x = * ]", "[ -e *.log ]", "[ a? = b ]")


@pytest.mark.parametrize("command", _GLOBS_RUN + _GLOBS_ASK_ANYWAY)
def test_a_glob_anywhere_in_a_bracket_or_test_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


@pytest.mark.parametrize(
    ("word", "glob"),
    [
        ("*", True),
        ("a?", True),
        ("'*'", False),
        ('"*"', False),
        ("\\*", False),
        ('"$x"*', True),
        ("x", False),
    ],
)
def test_an_unquoted_glob_in_any_part_of_a_word(word: str, glob: bool) -> None:
    node = bc._PARSER.parse(f"[ {word} ]".encode()).root_node.children[0].children[1]
    assert bc._has_unquoted_glob(node) is glob


# --- Fix round 2: a ksh pattern in ``[`` or ``test`` asks ---
#
# ksh93 and mksh expand ``!(…)`` and the other ksh patterns there, and
# tree-sitter reads ``!(x)`` as ``!`` and a parenthesised group, so the glob
# rule above missed them. With ``+1``, ``-eq`` and ``a[$(touch PWNED)]``
# planted, each of these created PWNED in macOS ksh93u+ and mksh (bash, zsh
# and dash reject them, Linux ksh 93u+m ran none); the ``[`` ones were ALLOW on
# ce3cc695.
_EXTGLOB_RUNS = (
    "[ !(x) ]",
    "[ ! !(x) ]",
    "[ !(x) = y ]",
    "[ !(x) -eq 1 ]",
    "[ 1 = 1 -a !(x) ]",
    "[ !(\\-) ]",
    "[ !(x) ] && echo hi",
    "test !(x)",  # ASK on ce3cc695 (a parse error)
    "test 1 = 1 -a !(x)",
    "[ 1 -o !(x) ]",  # macOS ksh93u+ only
)
# No shell ran these; each asks, the cost of the rule.
_EXTGLOB_ASKS = ("[ x = !(y) ]", "[ -e !(x) ]", "[ 1 -eq !(x) ]", "[ +(x) ]", "[ x != !(y) ]")
# Quoted, or under ``[[``, which does not expand them: no shell ran these.
_EXTGLOB_KEEPS = ('[ "!(x)" = y ]', "[ '!(x)' ]", "[[ x == !(y) ]]", "[[ $f == @(a|b) ]]")


@pytest.mark.parametrize("command", _EXTGLOB_RUNS + _EXTGLOB_ASKS)
def test_a_ksh_pattern_in_a_bracket_or_test_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


@pytest.mark.parametrize("command", _EXTGLOB_KEEPS)
def test_a_quoted_ksh_pattern_stays_allow(command: str) -> None:
    assert classify(command) == Verdict.ALLOW, command


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (b"[ !(x) ]", True),
        (b"[ @(x) ]", True),
        (b"[ +(x) ]", True),
        (b"[ ?(x) ]", True),
        (b"[ *(x) ]", True),
        (b"[ ~(E)^a ]", True),  # fix round 3
        (b'[ "!(x)" ]', False),
        (b"[ '+(x)' ]", False),
        (b"[ $'@(x)' ]", False),
        (b"[ ( x ) ]", False),
    ],
)
def test_what_counts_as_a_ksh_pattern(text: bytes, found: bool) -> None:
    node = bc._PARSER.parse(text).root_node.children[0]
    assert bc._has_extglob(node) is found


# A ``$((…))`` holding a ``$`` in quotes: tree-sitter keeps the ``$(…)`` in a
# string node. bash, zsh, dash and ksh93 created PWNED for the first two on both
# systems, and mksh too; macOS ksh93u+ alone for the double-quoted pair.
_ARITHMETIC_EXPANSIONS = (
    "[ 1 -eq $(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq $(( '$(touch PWNED)' )) ]]",
    '[ 1 -eq $(( "a[\\$(touch PWNED)]" )) ]',
    '[[ 1 -eq $(( "a[\\$(touch PWNED)]" )) ]]',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ARITHMETIC_EXPANSIONS)
def test_a_quoted_payload_in_an_arithmetic_expansion_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# Not a plain decimal number or a name; no shell ran anything (Codex: bash,
# zsh and ksh evaluate ``[[ 1 -eq '1,2' ]]`` as a comma expression).
_NOT_PLAIN = (
    "[ 1 -eq '1,2' ]",
    "[[ 1 -eq '1,2' ]]",
    "test 1 -eq '1,2'",
    "[[ 1 -eq '*' ]]",
    "[[ 1 -eq * ]]",
)


@pytest.mark.parametrize("command", _NOT_PLAIN)
def test_an_operand_that_is_not_a_plain_number_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


# --- Fix round 3: a token tree-sitter splits off an operand asks ---
#
# tree-sitter splits a sign, ``!``, ``~`` or ``(`` off an operand and reads
# ``-FOO`` as a ``test_operator``, so the word beside the arithmetic operator
# was a bare token the check skipped. Each row was ALLOW on 3963bea9 and
# 80ff2cc2 and created PWNED in at least one shell in the round-3 verify's
# runs (``.omc/probes/742/verify-r3/``; ``.omc/probes/743/finish/`` lists the
# shells per row): macOS bash 3.2.57 and ksh93u+, Linux bash 5.2.37 and mksh
# for most, Linux ksh 93u+m and zsh for some ``$((…))`` rows, macOS ksh93u+
# alone for the parenthesised ones, with ``FOO='a[$(touch PWNED)]'`` exported
# for the name rows. 99 of them are the verify's prefix and infix grids; the
# other 12 are its name and ksh-pattern rows.
_SPLIT_OFF_OPERANDS_RAN = (
    # A literal payload behind a sign, ``!``, ``~`` or ``-+``.
    "[[ 1 -eq +'a[$(touch PWNED)]' ]]",
    "[ 1 -eq +'a[$(touch PWNED)]' ]",
    "[[ 1 -eq -'a[$(touch PWNED)]' ]]",
    "[ 1 -eq -'a[$(touch PWNED)]' ]",
    "[[ 1 -eq !'a[$(touch PWNED)]' ]]",
    "[ 1 -eq !'a[$(touch PWNED)]' ]",
    "[[ 1 -eq ~'a[$(touch PWNED)]' ]]",
    "[ 1 -eq ~'a[$(touch PWNED)]' ]",
    "[[ 1 -eq -+'a[$(touch PWNED)]' ]]",
    "[ 1 -eq -+'a[$(touch PWNED)]' ]",
    '[[ 1 -eq +"a[\\$(touch PWNED)]" ]]',
    '[ 1 -eq +"a[\\$(touch PWNED)]" ]',
    '[[ 1 -eq -"a[\\$(touch PWNED)]" ]]',
    '[ 1 -eq -"a[\\$(touch PWNED)]" ]',
    '[[ 1 -eq !"a[\\$(touch PWNED)]" ]]',
    '[ 1 -eq !"a[\\$(touch PWNED)]" ]',
    '[[ 1 -eq ~"a[\\$(touch PWNED)]" ]]',
    '[ 1 -eq ~"a[\\$(touch PWNED)]" ]',
    '[[ 1 -eq -+"a[\\$(touch PWNED)]" ]]',
    '[ 1 -eq -+"a[\\$(touch PWNED)]" ]',
    "[[ 1 -eq +$'a[$(touch PWNED)]' ]]",
    "[ 1 -eq +$'a[$(touch PWNED)]' ]",
    "[[ 1 -eq -$'a[$(touch PWNED)]' ]]",
    "[ 1 -eq -$'a[$(touch PWNED)]' ]",
    "[[ 1 -eq !$'a[$(touch PWNED)]' ]]",
    "[ 1 -eq !$'a[$(touch PWNED)]' ]",
    "[[ 1 -eq ~$'a[$(touch PWNED)]' ]]",
    "[ 1 -eq ~$'a[$(touch PWNED)]' ]",
    "[[ 1 -eq -+$'a[$(touch PWNED)]' ]]",
    "[ 1 -eq -+$'a[$(touch PWNED)]' ]",
    # A name the environment holds.
    "[[ -FOO -eq 1 ]]",
    "[ -FOO -eq 1 ]",
    "[[ 1 -eq +FOO ]]",
    "[ 1 -eq +FOO ]",
    "[[ 1 -eq ~FOO ]]",
    "[ 1 -eq ~FOO ]",
    "[[ 1 -eq !(FOO) ]]",
    "[[ !(FOO) -eq 1 ]]",
    "[[ 1 -eq +(FOO) ]]",
    "[[ +(FOO) -eq 1 ]]",
    "[[ 1 -eq -'FOO' ]]",
    "[ 1 -eq -'FOO' ]",
    '[[ 1 -eq -"FOO" ]]',
    '[ 1 -eq -"FOO" ]',
    "[[ 1 -eq -+FOO ]]",
    "[ 1 -eq -+FOO ]",
    # A name bash fills from the line itself.
    "echo 'a[$(touch PWNED)]'; [[ 1 -eq +_ ]]",
    "echo 'a[$(touch PWNED)]'; [[ 1 -eq -_ ]]",
    "echo 'a[$(touch PWNED)]'; [[ 1 -eq ~_ ]]",
    "echo 'a[$(touch PWNED)]'; [[ 1 -eq -+_ ]]",
    "[[ 'a[$(touch PWNED)]' =~ .* ]]; [[ 1 -eq +BASH_REMATCH ]]",
    "[[ 'a[$(touch PWNED)]' =~ .* ]]; [[ 1 -eq -BASH_REMATCH ]]",
    "[[ 'a[$(touch PWNED)]' =~ .* ]]; [[ 1 -eq ~BASH_REMATCH ]]",
    "[[ 'a[$(touch PWNED)]' =~ .* ]]; [[ 1 -eq -+BASH_REMATCH ]]",
    # A ``$((…))`` behind a sign or in parentheses (some ran in all nine shells).
    "[[ 1 -eq -$((FOO)) ]]",
    "[ 1 -eq -$((FOO)) ]",
    "[[ 1 -eq +$(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq +$(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq -$(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq -$(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq !$(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq !$(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq ~$(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq ~$(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq - $(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq - $(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq + $(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq + $(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq ! $(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq ! $(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq ~ $(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq ~ $(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq -+$(( '$(touch PWNED)' )) ]]",
    "[ 1 -eq -+$(( '$(touch PWNED)' )) ]",
    "[[ 1 -eq ($(( '$(touch PWNED)' ))) ]]",
    "[ 1 -eq ($(( '$(touch PWNED)' ))) ]",
    "[ ($(( '$(touch PWNED)' ))) -eq 1 ]",
    # A parenthesised operand behind a sign (macOS ksh93u+).
    "[[ 1 -eq -(FOO) ]]",
    "[[ -(FOO) -eq 1 ]]",
    "[ 1 -eq -(FOO) ]",
    "[ -(FOO) -eq 1 ]",
    "[[ 1 -eq -($(( '$(touch PWNED)' ))) ]]",
    "[ 1 -eq -($(( '$(touch PWNED)' ))) ]",
    "[[ -($(( '$(touch PWNED)' ))) -eq 1 ]]",
    "[ -($(( '$(touch PWNED)' ))) -eq 1 ]",
    "[[ 1 -eq -('a[$(touch PWNED)]') ]]",
    "[ 1 -eq -('a[$(touch PWNED)]') ]",
    "[[ -('a[$(touch PWNED)]') -eq 1 ]]",
    "[ -('a[$(touch PWNED)]') -eq 1 ]",
    '[[ 1 -eq -("a[\\$(touch PWNED)]") ]]',
    '[ 1 -eq -("a[\\$(touch PWNED)]") ]',
    '[[ -("a[\\$(touch PWNED)]") -eq 1 ]]',
    '[ -("a[\\$(touch PWNED)]") -eq 1 ]',
    "[[ 1 -eq -($'a[$(touch PWNED)]') ]]",
    "[ 1 -eq -($'a[$(touch PWNED)]') ]",
    "[[ -($'a[$(touch PWNED)]') -eq 1 ]]",
    "[ -($'a[$(touch PWNED)]') -eq 1 ]",
    "[[ 1 -eq -('a[$(touch PWNED)]'+1) ]]",
    "[ 1 -eq -('a[$(touch PWNED)]'+1) ]",
    "[[ -('a[$(touch PWNED)]'+1) -eq 1 ]]",
    "[ -('a[$(touch PWNED)]'+1) -eq 1 ]",
    "[[ 1 -eq -(FOO+1) ]]",
    "[ 1 -eq -(FOO+1) ]",
    "[[ -(FOO+1) -eq 1 ]]",
    "[ -(FOO+1) -eq 1 ]",
    # The infix shapes.
    "[[ 1 -eq +'a[$(touch PWNED)]'+1 ]]",
    "[ 1 -eq +'a[$(touch PWNED)]'+1 ]",
    "[[ 1 -eq +FOO+1 ]]",
    "[ 1 -eq +FOO+1 ]",
    # A ksh93 ``~(…)`` pattern (macOS ksh93u+, files planted).
    "[ 1 -eq ~(E)^a ]",
    "[ 1 -eq ~(E)^a. ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _SPLIT_OFF_OPERANDS_RAN)
def test_a_token_split_off_an_operand_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


def test_the_split_off_rows_are_the_verifys() -> None:
    assert len(_SPLIT_OFF_OPERANDS_RAN) == len(set(_SPLIT_OFF_OPERANDS_RAN)) == 111


# A sign before a plain number, and a bracket, ``!``, ``(``, ``&&``, ``||``,
# ``-a`` or ``-o`` left of the operator, are no operand of their own: these
# stay ALLOW, as on 3963bea9.
_SPLIT_OFF_KEEPS = (
    "[[ 1 -eq -1 ]]",
    "[ -1 -lt 0 ]",
    "[[ -1 -lt 0 ]]",
    "[ ! 1 -eq 2 ]",
    "[[ ! 1 -eq 2 ]]",
    "[ ( 1 -eq 2 ) ]",
    "[[ ( 1 -eq 2 ) ]]",
    "[ x = y -a 1 -eq 2 ]",
    "[ -n x -a 1 -eq 2 ]",
    "[ 1 -eq 2 -o 3 -lt 4 ]",
    "[[ x == y && 1 -eq 2 ]]",
    "[[ x == y || 1 -eq 2 ]]",
    "[ $# -gt -1 ]",
    "[[ 1 -ne +2 ]]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _SPLIT_OFF_KEEPS)
def test_a_sign_before_a_number_or_a_bracket_stays_allow(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


@pytest.mark.parametrize(
    ("words", "j", "left", "plain"),
    [
        (["[[", "1", "-eq", "-", "1", "]]"], 3, False, True),
        (["[[", "1", "-eq", "+", "1", "]]"], 3, False, True),
        (["[[", "1", "-eq", "+", "FOO", "]]"], 3, False, False),
        (["[[", "1", "-eq", "~", "1", "]]"], 3, False, False),
        (["[[", "1", "-eq", "-", "]]"], 3, False, False),
        (["[", "!", "1", "-eq", "2", "]"], 1, True, True),
        (["[", "x", "-", "-eq", "1", "]"], 2, True, False),  # a sign on the left
        (["[", "1", "-eq", "-"], 3, False, False),
    ],
)
def test_which_split_off_token_is_plain(words: list[str], j: int, left: bool, plain: bool) -> None:
    nodes = [_Tok(w) for w in words]
    assert bc._split_off_operand_is_plain(nodes, j, left=left) is plain


class _Tok:
    """A stand-in word: its ``type`` is the token, or ``number`` for digits."""

    def __init__(self, text: str) -> None:
        self.type = "number" if text.isdigit() else text


@pytest.mark.parametrize("token", ["[", "[[", "!", "(", "&&", "||", "-a", "-o"])
def test_each_plain_left_token(token: str) -> None:
    assert bc._split_off_operand_is_plain([_Tok(token), _Tok("-eq")], 0, left=True)


# Fix round 3 adds ksh93's ``~(…)`` to the ksh patterns: with the three files
# planted, ``[ 1 -eq ~(E)^a ]`` ran ``touch`` in macOS ksh93u+ (above). No
# shell ran these, which ask now; quoted or under ``[[`` they stay ALLOW.
_KSH_TILDE_ASKS = ("[ ~(E)^a ]", "[ ~(E)^a = x ]", "[ 1 = 1 -a ~(E)^a ]", "[ ~(x) ]")
_KSH_TILDE_KEEPS = ('[ "~(E)^a" = x ]', "[[ x == ~(E)^a ]]", "[ -d ~/x ]", "[ ~/x = x ]")


@pytest.mark.parametrize("command", _KSH_TILDE_ASKS)
def test_a_ksh_tilde_pattern_in_a_bracket_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


@pytest.mark.parametrize("command", _KSH_TILDE_KEEPS)
def test_a_quoted_or_double_bracket_tilde_pattern_stays_allow(command: str) -> None:
    assert classify(command) == Verdict.ALLOW, command


# --- The entry points that act on the verdict ---


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


# Ran with no dialog in a pty TUI under ``auto`` on b160b8f7 and created PWNED
# (``SHELL=/bin/bash`` for the first, ``/bin/ksh`` for the second).
_GATE_ASK = (
    "[[ 1 -eq 'a[$(touch PWNED)]' ]]",
    "[ 1 -eq 'a[$(touch PWNED)]' ]",
    # Fix round 1: ran with no dialog on 61eda564 and created PWNED (bash, ksh
    # with the files planted, and all four shells).
    "[[ 'a[$(touch PWNED)]' =~ .* && BASH_REMATCH -eq 0 ]]",
    "[ * ]",
    "[ 1 -eq $(( '$(touch PWNED)' )) ]",
    # Fix round 2: ran with no dialog on ce3cc695 (ksh with the files planted;
    # bash and ksh with a work folder .env holding EVIL_API_KEY).
    "[ !(x) ]",
    "[[ EVIL_API_KEY -eq 0 ]]",
    "[ 1 -eq EVIL_API_KEY ]",
    # Fix round 3: in the round-3 verify's TUI run these ran with no dialog on
    # 3963bea9 and 80ff2cc2 and created PWNED (bash, ksh, and bash with a work
    # folder .env holding EVIL_API_KEY).
    "[[ 1 -eq +'a[$(touch PWNED)]' ]]",
    "[ 1 -eq -'a[$(touch PWNED)]' ]",
    "[[ 1 -eq +EVIL_API_KEY ]]",
)
_GATE_READS = ('[ "$a" = "$b" ]', "[[ -f x ]]", "[ 1 -eq 2 ]")


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

    ``-p`` running an ASK is ADR-0120's rule and pre-existing, so these forms still
    run there.
    """

    _pin_shell(monkeypatch, "/bin/ksh")
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
