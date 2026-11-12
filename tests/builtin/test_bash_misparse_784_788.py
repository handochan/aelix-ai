"""#784–#789 — six more places tree-sitter reads otherwise than the shells.

The tree-sitter-vs-shell differential audit on 52bc607a (``.omc/probes/ts-audit/``)
found the first five as ALLOW while the shells ran a hidden command or redirect;
the implementation lane's re-run of its generators found the sixth:

- #784: tree-sitter pairs a ``{`` in a ``${x#…}`` pattern with a later ``}``;
  bash, dash and mksh end the ``${…}`` at the first ``}`` and run what follows.
- #785: tree-sitter keeps ``>MARK`` after ``=~`` in ``[ … ]`` as a pattern
  leaf; every shell but zsh redirects there (dash in ``[[ … ]]`` too). Fix
  round 1: ``;``, ``|`` and ``&`` there too, and between test operands; fix
  round 2: not ``&&``, after which no shell ran anything, unless (the
  finish) a word before it is a ``]`` once its quotes are removed, which
  ends the ``[``.
- #786: zsh runs ``=(…)`` at the start of a ``${x:-…}`` operand word, and
  (fix round 1) where brace expansion starts one, (fix round 2) after a
  ``$y`` there that may be empty too, and (the finish) after ``$*``,
  ``$@``, ``$=y``, ``$^y``, ``$~y``, ``$y[1]`` or ``$''``.
- #787: tree-sitter splits a ``$'…'`` in a quoted ``${…}`` operand into ``$``
  and ``'…'``; bash 3.2 decodes it and runs what it decoded.
- #788: dash, ksh93 and mksh have no ``$[…]``, and dash no ``(( … ))``; there
  they are read as commands. Fix round 1: that reading counts only when it is
  DENY (the owner's decision of 2026-11-12, as the lead read it).
- #789 (fix round 1): tree-sitter ends a ``${…}`` at an escaped ``}`` in double
  quotes, and bash runs a ``<(…)`` after it in the operand, (fix round 2)
  reading on past a quoted ``}``, and (the finish) past a ``${…}`` in a
  nested ``"…"``.

Every shell row was measured with ``<shell> -c`` in a fresh scratch directory
under ``env -i``: macOS bash 3.2.57, zsh 5.9, dash and ksh93u+ 2012-08-01 (each
under ``sandbox-exec``), and Linux (docker ``debian:trixie``) bash 5.2.37, zsh
5.9, dash 0.5.12, ksh 93u+m/1.0.10 and mksh 59c
(``.omc/probes/784-788/impl/shells/``, ``.omc/probes/784-788/fix-r1/shells/``,
``.omc/probes/784-788/fix-r2/ev/``).
The comment on a row names the shells that created a file. The ``rm -rf``,
``find . -delete``, ``| sh`` and ``~/.bashrc`` rows were only classified, never
run.

Verdicts are compared with ``==``, not ``is``: ``test_bash_classifier.py``
reloads the classifier module last, which rebinds ``Verdict``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
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
_ORDER = {Verdict.ALLOW: 0, Verdict.ASK: 1, Verdict.DENY: 2}


def _both(command: str) -> tuple[Verdict, Verdict]:
    return classify(command, dialect=Dialect.UNKNOWN), classify(command, dialect=Dialect.POSIX)


def _parse(source: bytes) -> Any:
    assert bc._PARSER is not None
    return bc._PARSER.parse(source).root_node


def _misread(source: bytes, dialect: Dialect) -> Any:
    return bc._misread_edits(_parse(source), source, dialect=dialect)


# --- #784: a ``{`` in a pattern operand ----------------------------------------------

# Each was ALLOW on 52bc607a and created ``MARK}`` (``MARK}b`` for the last)
# in macOS bash 3.2.57 and dash and Linux bash 5.2.37, dash 0.5.12 and mksh 59c.
_BRACE_RUNS = (
    "echo ${x#{}>MARK}",
    "echo ${x##{}>MARK}",
    "echo ${x%{}>MARK}",
    "echo ${x%%a{}>MARK}",
    "echo ${x#{}| : > MARK}",
    "echo ${x#{};: > MARK}",
    "echo ${x#{}& : > MARK}",
    "echo ${x#{a}>MARK}",
    "echo ${x#a{b}c>MARK}",
    "echo ${x#{{}}>MARK}",
    "echo ${x#[{]}>MARK}",
    "echo ${x#({)}>MARK}",
    "echo a${x#{}>MARK}b",
    "echo ${x^{}>MARK}",  # Linux bash 5.2.37 alone (bash 3.2 has no ``^``)
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BRACE_RUNS)
def test_what_follows_the_first_brace_is_read_as_code(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# Classified only; ALLOW on 52bc607a. ``| rm -rf ~}`` passes ``~}``, which is
# no home directory, and is refused as ``rm -rf`` is anywhere.
_BRACE_DENIED = (
    "echo ${x#{}| rm -rf ~}",
    "echo ${x#{};rm -rf ~ }",
    "echo ${x#{}& find . -delete }",
    "echo ${x#{};: >~/.bashrc;}",
    "echo ${x#{}>~/.bashrc }",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BRACE_DENIED)
def test_a_brace_twin_is_refused(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


# ALLOW on 52bc607a and still ALLOW. No shell ran anything in the first three
# (in double quotes what follows the ``}`` is text; ``<`` reads a file).
_BRACE_STAYS = (
    'echo "${x#{};: > MARK;}"',
    "cat ${x#{}<MARK}",
    "echo ${x#{}}",
    "echo ${x#a{b}c}",
    "echo ${x:-{a,b}}",
    "echo ${x#\\{}",
    "echo ${x//[/]/_}",
    "echo ${x%/*} ${path##*/} ${f%.txt} ${x#*.}",
    'echo "${x%%\\{*}"',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BRACE_STAYS)
def test_an_ordinary_pattern_operand_stays_allowed(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


def test_the_brace_reading_replaces_the_expansion_up_to_the_first_brace() -> None:
    source = b"echo a${x#{};: > MARK}b"
    misread = _misread(source, Dialect.UNKNOWN)
    assert misread.shells == []
    assert bc._edited(source, misread.more) == b"echo ax;: > MARK}b"
    plain = b"echo ${x#a}"
    assert bc._early_close(_expansion(plain), plain) == []


def _expansion(source: bytes) -> Any:
    stack = [_parse(source)]
    while stack:
        node = stack.pop()
        if node.type == "expansion":
            return node
        stack.extend(node.children)
    raise AssertionError(source)


# --- Fix round 1: a #784 edit over a 52bc607a one ------------------------------------

# The round-1 verify's F2: DENY on 52bc607a, ASK on a9f39aa2. The #784 edit
# replaced the ``${…}`` up to its ``}`` and dropped #768's edit of the
# ``"$ $(…)"`` inside it, so no reading held the ``rm``. Each stays refused:
# the edits 52bc607a made are read on their own, as there. The ``: > MARK``
# twin of the first created MARK in all nine shells (the verify's
# ``atk/f2.txt``).
_OVERLAP_DENIED = tuple(
    f'echo {quote}${{{name}{op}"${blank}$({payload})"{{}}{tail}}}{quote}'
    for quote in ("", '"')
    for name, op in (("x", "#"), ("PWD", "%%"), ("x", "/"))
    for blank in (" ", "\t", "\n")
    for payload, tail in (("rm -rf ~", ""), (": >~/.bashrc", "a"))
) + (
    'echo ${x:-${y#"$ $(rm -rf ~)"{}}}',
    'echo ${x#"$ $(find / -delete)"{}}',
    'cat <<E\n${x#"$ $(rm -rf ~)"{}}\nE',
    "echo ${x#$ $(rm -rf ~){}}",
    "echo ${x#a$ $(rm -rf ~){}}",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _OVERLAP_DENIED)
def test_a_brace_edit_hides_no_earlier_reading(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


def test_the_two_edit_sets_are_read_apart() -> None:
    source = b'echo ${PWD#"$ $(rm -rf ~)"{}}'
    misread = _misread(source, Dialect.UNKNOWN)
    assert bc._edited(source, misread.shells) == b'echo ${PWD#"\\$ $(rm -rf ~)"{}}'
    assert bc._edited(source, misread.shells + misread.more) == b"echo x}"


# --- #785: an operator in a ``[ … =~ … ]`` pattern leaf -------------------------------

# ALLOW on 52bc607a; each created MARK in macOS bash 3.2.57, dash and ksh93u+
# and Linux bash 5.2.37, dash 0.5.12, ksh 93u+m and mksh 59c (not zsh).
_BRACKET_RUNS = (
    "[ x =~ >MARK ]",
    "[ x =~ a>MARK ]",
    "[ -n x =~ >MARK ]",
    "[ x =~ >>MARK ]",
    "[ x =~ a>|MARK ]",
    "[ x =~ 2>MARK ]",
    "[ x =~ \\\\>MARK ]",
    "[ x =~ >MARK ] ; echo",
)

# Fix round 1 (the round-1 cross-review's finding 1): a ``;``, ``|``,
# ``||`` or ``&`` in the leaf, or a ``|`` tree-sitter reads between test
# operands. ALLOW on 52bc607a and a9f39aa2; each created MARK in macOS bash
# 3.2.57 and ksh93u+ and Linux bash 5.2.37, ksh 93u+m and mksh 59c (dash has
# no brace expansion, zsh rejects the line;
# ``.omc/probes/784-788/fix-r1/shells/``).
_BRACKET_LIST_RUNS = (
    "[ x =~ a;{sh,-c,:\\>MARK};b ]",
    "[ x =~ a|{sh,-c,:\\>MARK} ]",
    "[ x =~ a||{sh,-c,:\\>MARK} ]",
    "[ x =~ a&{sh,-c,:\\>MARK} ]",
    "[ x == a|{sh,-c,:\\>MARK} ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BRACKET_RUNS + _BRACKET_LIST_RUNS)
def test_an_operator_in_a_bracket_pattern_is_read_as_one(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# Classified only; ALLOW on 52bc607a and a9f39aa2. The round-1 cross-review's
# brace forms (``a;{rm,-rf,~};b``, ``a|{rm,-rf,~}``, …) ask and are not
# refused: a brace-expanded command word, ``{rm,-rf,~}`` alone included, is
# ASK on 52bc607a, and nothing here changes that.
_BRACKET_LIST_ASKED = (
    "[ x =~ a;{rm,-rf,~};b ]",
    "[ x =~ a|{rm,-rf,~} ]",
    "[ x =~ a||{rm,-rf,~} ]",
    "[ x =~ a&{rm,-rf,~} ]",
    "[ x =~ a;{find,.,-delete};b ]",
)
_BRACKET_LIST_DENIED = (
    "[ x =~ a|sh ]",
    "[ x =~ a;>~/.bashrc;b ]",
    "[ x =~ a||>~/.bashrc ]",
    "[ x == a|sh ]",
    "[ a | sh ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
def test_a_list_twin_in_a_bracket_pattern_asks_or_is_refused(dialect: Dialect) -> None:
    for command in _BRACKET_LIST_ASKED:
        assert classify(command, dialect=dialect) == Verdict.ASK, command
    for command in _BRACKET_LIST_DENIED:
        assert classify(command, dialect=dialect) == Verdict.DENY, command
    assert classify("{rm,-rf,~}", dialect=dialect) == Verdict.ASK


# ALLOW on 52bc607a; MARK in macOS dash and Linux dash alone, which have no
# ``[[``, so these ask under the POSIX dialect, as ``[[ a > MARK ]]`` does.
_DOUBLE_BRACKET_RUNS = ("[[ x =~ >MARK ]]", "[[ x = >MARK ]]", "[[ x =~ ^a>MARK ]]")


@pytest.mark.parametrize("command", _DOUBLE_BRACKET_RUNS)
def test_double_brackets_read_it_where_dash_may_run(command: str) -> None:
    assert _both(command) == (Verdict.ALLOW, Verdict.ASK), command


def test_a_protected_target_in_a_bracket_pattern_is_refused() -> None:
    assert _both("[ x =~ >~/.bashrc ]") == (Verdict.DENY, Verdict.DENY)
    assert _both("[ x =~ a>~/.ssh/authorized_keys ]") == (Verdict.DENY, Verdict.DENY)
    assert _both("[[ x =~ >~/.bashrc ]]") == (Verdict.ALLOW, Verdict.DENY)
    assert _both("[[ x =~ a;>~/.bashrc;b ]]") == (Verdict.ALLOW, Verdict.DENY)


# Fix round 1, the lead's reading of the owner's #788 decision applied by
# this lane to ``[[ … ]]``: only dash, which has no ``[[``, reads a ``;``,
# ``|`` or ``&`` there as the shell's, so that reading counts only when it is
# DENY. Read in full, it made 4 of the 31,958, 12 of the 16,649 and 10 of
# the 14,428 real lines ask under POSIX, such as
# ``[[ -f "${file}" && -x "${file}" ]]`` and ``[[ $x =~ ^v[0-9]|stable$ ]]``
# (``.omc/probes/784-788/fix-r1/cost/double-bracket-full-reading.txt``).
_DOUBLE_BRACKET_LIST_DENIED = ("[[ x == a|sh ]]", "[[ x =~ a|sh ]]", "[[ a | sh ]]")
_DOUBLE_BRACKET_LIST_ALLOWED = (
    "[[ x =~ a;reboot;b ]]",  # dash would run ``reboot``; documented, not covered
    "[[ x == a|reboot ]]",
    "[[ x =~ a;{sh,-c,:\\>MARK};b ]]",  # no shell measured ran it
    "[[ $x =~ ^-h|--help$ ]]",
    "[[ -n $a && -n $b ]]",
    "[[ $x =~ ^(a|b)$ ]]",
    "[[ $f == @(a|b) ]]",
    # A ``<`` is an input redirect to dash; none of these wrote a file there.
    "[[ $x =~ !<MARK ]]",
    "[[ x =~ a<<E ]]",
)


def test_double_brackets_count_dashs_list_only_when_it_is_refused() -> None:
    for command in _DOUBLE_BRACKET_LIST_DENIED:
        assert _both(command) == (Verdict.ALLOW, Verdict.DENY), command
    for command in _DOUBLE_BRACKET_LIST_ALLOWED:
        assert _both(command) == (Verdict.ALLOW, Verdict.ALLOW), command


# ALLOW on 52bc607a and still ALLOW: no operator, an escaped or quoted one, one
# in a substitution or a group, or a read.
_BRACKET_STAYS = (
    "[ x =~ <MARK ]",
    "[ x =~ a\\>MARK ]",
    "[[ $x =~ \\> ]]",
    "[[ $x =~ ^a ]]",
    "[[ $a == $b ]]",
    "[[ $x =~ ^[0-9]+$ ]]",
    '[ "$a" = "$b" ]',
    "[ a < b ]",
    '[[ "$v" =~ ^v([0-9]+) ]] && echo ok',
    "[ x =~ a\\;b ]",
    "[ x =~ 'a;b' ]",
    '[ x =~ "a|b" ]',
    "[ x =~ $'a\\';b' ]",
    "[ x =~ (a|b) ]",
    '[ -n "$a" ] && [ -n "$b" ]',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BRACKET_STAYS)
def test_an_ordinary_bracket_pattern_stays_allowed(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# Fix round 2 (the round-2 verify's F4): after an ``&&`` in a ``[ … ]`` or
# ``[[ … ]]``, between operands or in a pattern leaf, with no ``]`` word
# before it, no shell ran anything: ``[ -n a && touch MARK ]``,
# ``[[ -n a && touch MARK ]]`` and ``[ x =~ a&&touch MARK ]`` created no file
# in any of the nine (``.omc/probes/784-788/fix-r2/ev/f4.txt``), and the
# ``mkdir -p MARK`` rows here none either
# (``.omc/probes/784-788/finish/ev/f1-test-rows.txt``). ALLOW on 52bc607a;
# f5d61467 asked about or refused the first five.
_TEST_AND_STAYS = (
    '[ -n "$a" && -n "$b" ]',
    '[ "$a" = 1 && "$b" = 2 ]',
    "[ -n a && rm -rf /home ]",
    "[[ -n a && rm -rf /home ]]",
    "[ x =~ a&&rm -rf /home ]",
    "[ -n '\\]' && mkdir -p MARK ]",
    '[ -n "\\]" && mkdir -p MARK ]',
    "[[ -n \\] && mkdir -p MARK ]]",
)
# The finish (the round-3 verify's F1): a ``]`` the shells see before the
# ``&&`` ends the ``[``, which succeeds, and the shells run what follows.
# ALLOW on 52bc607a and 5be3fc88; each created MARK in the shells named
# (``.omc/probes/784-788/finish/ev/f1-test-rows.txt``).
_TEST_AND_RUNS = (
    "[ -n \\] && mkdir -p MARK ]",  # all nine
    "[ -n ']' && mkdir -p MARK ]",  # all nine
    '[ -n "]" && mkdir -p MARK ]',  # all nine
    "[ -n ''']' && mkdir -p MARK ]",  # all nine
    "[ -n $']' && mkdir -p MARK ]",  # all but dash
    "[ -n $'\\x5d' && mkdir -p MARK ]",  # all but dash
    "[ -n \\]x && mkdir -p MARK ]",  # dash, whose ``[`` reads one byte of its last argument
    "[ -n =~ \\]&&mkdir -p MARK ]",  # all but zsh; in a pattern leaf
    "[ -n == ']'&&mkdir -p MARK ]",  # all but zsh; in a pattern leaf
    '[ -n x -a -n =~ "]"&&mkdir -p MARK ]',  # all but zsh; in a pattern leaf
)
# ``||``, ``|`` and ``&`` there end the ``[`` too: the ``touch MARK`` twins
# of the first three created MARK in all nine, so these stay refused, the
# ``&`` row pinning mutant R32 of the round-2 verify. The last refuses the
# ``| sh`` f5d61467 refused, though ``[ x =~ a&&b|touch MARK ]`` created no
# file in any of the nine (``.omc/probes/784-788/finish/ev/f1-test-rows.txt``).
_TEST_LIST_DENIED = (
    "[ -n a & rm -rf /home ]",
    "[ -n a || rm -rf /home ]",
    "[ -n a | sh ]",
    "[ x =~ a&&b|sh ]",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
def test_an_and_list_in_a_bracket_runs_nothing_and_stays_allowed(dialect: Dialect) -> None:
    for command in _TEST_AND_STAYS:
        assert classify(command, dialect=dialect) == Verdict.ALLOW, command
    for command in _TEST_LIST_DENIED:
        assert classify(command, dialect=dialect) == Verdict.DENY, command


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _TEST_AND_RUNS)
def test_an_and_list_after_a_bracket_word_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace("mkdir -p MARK", "rm -rf /home")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


def test_a_bracket_word_is_read_with_its_quotes_removed() -> None:
    assert bc._bracket_word(b"[ -n a && b ]", 1) == 12
    assert bc._bracket_word(b"[ -n a ''\\] && b", 1) == 7
    assert bc._bracket_word(b"[ x'y ]' \"]\" && b", 1) == 9
    for word in (b"]", b"\\]", b"']'", b'"]"', b"$']'", b'$"]"', b"''\\]", b"\"\"$'\\x5d'"):
        assert bc._opens_with_bracket(word + b"x", 0), word
    assert bc._opens_with_bracket(b"$''']'", 0)
    for word in (b"'\\]'", b'"\\]"', b"$'\\]'", b"a]", b"''", b"$'\\x5c'", b'"$a"'):
        assert not bc._opens_with_bracket(word, 0), word
    assert bc._holds_an_operator(b"a&&b") == frozenset()
    assert bc._holds_an_operator(b"a&&b", 0) == frozenset(b"&")
    assert bc._holds_an_operator(b"a&&b", 1) == frozenset()
    assert bc._holds_an_operator(b"a&&b", -1) == frozenset(b"&")


# Fix round 1 (the round-1 cross-review's findings 3 and 7): a quoted ``<``
# or ``>`` is text to the shells. a9f39aa2 quoted the operator for these and
# lost the regex 52bc607a read, so the first ten went below 52bc607a (16
# verdicts; ``.omc/probes/784-788/cross-r1/lower785.json``), and the last two
# asked under POSIX. Each reads as on 52bc607a again.
_QUOTED_LEAF_AS_BEFORE = (
    ("[ ! x =~ '>'<] ]", (Verdict.ASK, Verdict.ASK)),
    ("[ ! x =~ '<'<} ]", (Verdict.ASK, Verdict.ASK)),
    ("[[ x =~ <\\> <-a ]]", (Verdict.ALLOW, Verdict.ASK)),
    ("[ ! x =~ \\('<'<] ]", (Verdict.ASK, Verdict.ASK)),
    ("[ ! x =~ '>'</}/ ]", (Verdict.ASK, Verdict.ASK)),
    ("[[ x =~ '<''<'\\('>'<} ]]", (Verdict.ALLOW, Verdict.ASK)),
    ("[ x =~ :'<' <~x ]", (Verdict.ASK, Verdict.ASK)),
    ("[ x =~ '>'<] ]", (Verdict.ASK, Verdict.ASK)),
    ("[ x = y -o x =~ '>'<}\">\" ]", (Verdict.ASK, Verdict.ASK)),
    ("[[ x =~ '>'-a<\\)] ]]", (Verdict.ALLOW, Verdict.ASK)),
    ("[[ $x =~ '>' ]]", (Verdict.ALLOW, Verdict.ALLOW)),
    ("[[ $x =~ '<'(.*)'>' ]]", (Verdict.ALLOW, Verdict.ALLOW)),
)


@pytest.mark.parametrize(("command", "verdicts"), _QUOTED_LEAF_AS_BEFORE)
def test_a_quoted_operator_in_a_pattern_leaf_is_text(
    command: str, verdicts: tuple[Verdict, Verdict]
) -> None:
    assert _both(command) == verdicts, command


def test_the_bracket_reading_quotes_the_operator() -> None:
    source = b"[ x =~ >MARK ]"
    misread = _misread(source, Dialect.UNKNOWN)
    assert misread.shells == [] and misread.dash == []
    assert bc._edited(source, misread.more) == b"test x '=~' >MARK  "
    source = b"[[ x =~ a;b ]]"
    misread = _misread(source, Dialect.POSIX)
    assert misread.more == []
    assert bc._edited(source, misread.dash) == b"test x '=~' a;b   "
    assert _misread(source, Dialect.UNKNOWN).dash == []


def test_the_leaf_scan_skips_quotes_escapes_and_groups() -> None:
    assert bc._holds_an_operator(b"a;b|c&d<e>f\ng") == frozenset(b";|&<>\n")
    assert bc._holds_an_operator(b"a\\;'|'\"&\"$'\\';'$(a>b)`a<b`${a:-;}(a|b)") == frozenset()
    assert bc._holds_an_operator(b"'>'<]") == frozenset(b"<")
    assert bc._holds_an_operator(b"a<<E") == frozenset(b"<")


# --- #786: zsh's ``=(…)`` in a ``${…}`` operand ---------------------------------------

# ALLOW on 52bc607a; each created MARK in macOS zsh 5.9 and Linux zsh 5.9.
# Fix round 1 (the round-1 verify's F1): brace expansion starts a word too,
# after ``{`` or ``,``; the last four were ALLOW on a9f39aa2 as well.
_ZSH_RUNS = (
    "echo ${x:-=(: > MARK)}",
    "echo ${x-=(: > MARK)}",
    "echo ${x/a/=(: > MARK)}",
    "echo ${x:-=(: > MARK)b}",
    "echo ${x:-${y:-=(: > MARK)}}",
    "echo ${x:-=(: > MARK)$y}",
    "echo ${x:-{a,=(: > MARK)}}",
    "echo ${x:-{,=(: > MARK)}}",
    "echo ${x-{a,=(: > MARK)}}",
    "echo ${x:-$y{a,=(: > MARK)}}",
    "echo ${x:-{\\\\,=(: > MARK)}}",  # ``\\`` is a backslash; the ``,`` still splits
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ZSH_RUNS)
def test_a_zsh_substitution_in_an_operand_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# Classified only; ALLOW on 52bc607a. zsh drops each ``-`` of the words in
# such a ``=(…)`` (``cat ${x:-=(printf %s, a -b x)}`` printed ``a,b,x,``), so
# its ``rm -rf ~`` would run as ``rm rf ~``; the text is read with them kept.
_ZSH_DENIED = (
    "echo ${x:-=(rm -rf ~)}",
    "echo ${x:-=(find . -delete)}",
    "echo ${x-=(: > ~/.bashrc)}",
    "x=1; echo ${x:+=(rm -rf ~)}",
    "echo ${x:-=(rm -rf ~)$y}",
    "echo ${x:-{a,=(rm -rf ~)}}",
    "echo ${x:-{=(rm -rf ~),a}}",
    "echo ${x:-=<(rm -rf ~)}",  # DENY on 52bc607a already: ``<(…)`` is parsed
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ZSH_DENIED)
def test_a_zsh_substitution_twin_is_refused(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


# Fix round 2 (the round-2 verify's F2): after a ``{`` or ``,`` and a ``$y``
# or ``${y}``, which may be empty. ALLOW on 52bc607a and f5d61467; each
# created MARK in macOS zsh 5.9 and Linux zsh 5.9
# (``.omc/probes/784-788/fix-r2/ev/f2-zsh-onf5.txt``, ``f2-zsh2-onf5.txt``).
_ZSH_EMPTY_RUNS = (
    "echo ${x:-{a,$y=(: > MARK)}}",
    "echo ${x:-{a,${y}=(: > MARK)}}",
    "echo ${x:-{a,$y$z=(: > MARK)}}",
    "echo ${x:-{a,$y=(: > MARK),b}}",
    "echo ${x-{a,$y=(: > MARK)}}",
    "echo ${x:-{a,$y=(: > MARK)}b}",
    "echo ${x:-{a,b,$y=(: > MARK)}}",
    "echo ${x:-{a,{b,$y=(: > MARK)}}}",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ZSH_EMPTY_RUNS)
def test_a_zsh_substitution_after_an_empty_expansion_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


# The finish (the round-3 verify's F3): after a ``{`` or ``,`` and an
# expansion tree-sitter keeps as word text, a ``$y[…]`` subscript or a
# ``$''``, each of which may be empty. ALLOW on 52bc607a and 5be3fc88; each
# created MARK in macOS zsh 5.9 and Linux zsh 5.9
# (``.omc/probes/784-788/finish/ev/f3-rows.txt``).
_ZSH_TEXT_EMPTY_RUNS = (
    "echo ${x:-{a,$*=(: > MARK)}}",
    "echo ${x:-{a,$@=(: > MARK)}}",
    "echo ${x:-{a,$=y=(: > MARK)}}",
    "echo ${x:-{a,$^y=(: > MARK)}}",
    "echo ${x:-{a,$~y=(: > MARK)}}",
    "echo ${x:-{a,$^=y=(: > MARK)}}",
    "echo ${x:-{a,$y[1]=(: > MARK)}}",
    "echo ${x:-{a,$y[1,2]=(: > MARK)}}",
    "echo ${x:-{a,$*[1]=(: > MARK)}}",
    "echo ${x:-{a,$y[1]$z=(: > MARK)}}",
    "echo ${x:-{a,$''=(: > MARK)}}",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ZSH_TEXT_EMPTY_RUNS)
def test_a_zsh_substitution_after_an_empty_text_expansion_asks(
    command: str, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


# ALLOW on 52bc607a and still ALLOW. zsh ran no ``=(…)`` in double quotes,
# after other text in the word, after a ``$y`` or ``${y}`` that starts the
# operand, or in a pattern operand (``${x#=(…)}``); nor (fix round 2) the
# ``: > MARK`` twins of the last three (macOS and Linux zsh 5.9).
_ZSH_STAYS = (
    'echo "${x:-=(: > MARK)}"',
    "echo ${x:-a=(b)}",
    "echo ${x:-${y}=(b)}",
    "echo ${x:-=}",
    "echo ${x:-=a}",
    "echo ${x#=(b)}",
    "echo ${x:-$y=(rm -rf ~)}",
    "echo ${x:-{a,b$y=(rm -rf ~)}}",
    "echo ${x:-{a,\\$y=(rm -rf ~)}}",
    # The finish: an escaped ``\$*``, a ``$*`` that starts the operand or
    # follows ``b``, and ``$+y`` and ``$#``, which are never empty; zsh ran
    # none of these ``rm -rf ~`` rows' ``: > MARK`` twins.
    "echo ${x:-{a,\\$*=(rm -rf ~)}}",
    "echo ${x:-$*=(rm -rf ~)}",
    "echo ${x:-{a,b$*=(rm -rf ~)}}",
    "echo ${x:-{a,$+y=(rm -rf ~)}}",
    "echo ${x:-{a,$#=(rm -rf ~)}}",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _ZSH_STAYS)
def test_an_equals_sign_zsh_does_not_substitute_stays_allowed(
    command: str, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


def test_an_equals_sign_after_a_comma_outside_braces_is_not_read_as_zshs() -> None:
    """ASK on 52bc607a, as any ``=(`` after other text in the word; zsh ran none of these."""

    assert _both("echo ${x:-a,=(: > MARK)}") == (Verdict.ASK, Verdict.ASK)
    assert _both("echo ${x:-a,=(rm -rf ~)}") == (Verdict.ASK, Verdict.ASK)


# --- #787: a ``$'…'`` split into ``$`` and ``'…'`` -------------------------------------

# ALLOW on 52bc607a; each created MARK in macOS bash 3.2.57 (not Linux bash
# 5.2.37), as did 436 more of the 564 quoted spellings that parse with the
# split (``.omc/probes/784-788/impl/m787/``).
_SPLIT_RUNS = (
    'echo "a${PWD%$\'}b"\\x24(: > MARK)\'}b"',
    "echo \"a${PWD#$'}\\x24(: > MARK)'}b\"",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _SPLIT_RUNS)
def test_a_split_dollar_quote_is_read_decoded(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    for payload in ("rm -rf ~", "find . -delete"):
        twin = command.replace(": > MARK", payload)
        assert classify(twin, dialect=dialect) == Verdict.DENY, twin


def test_a_split_dollar_quote_asks_as_an_unsplit_one_does() -> None:
    """The cost: a quoted ``$'…'`` in a pattern operand already asked whole (#768)."""

    assert _both("echo \"${PWD%$'abc'}\"") == (Verdict.ASK, Verdict.ASK)
    assert _both("echo \"${PWD%$'}'}\"") == (Verdict.ASK, Verdict.ASK)  # ALLOW on 52bc607a
    # Not in double quotes bash does not expand the decoded text again.
    assert _both("echo ${PWD%$'}\\x24(: > MARK)'}") == (Verdict.ALLOW, Verdict.ALLOW)
    assert _both('echo "${x%$v}" "${x#"$y"}" "${x%\\$}"') == (Verdict.ALLOW, Verdict.ALLOW)
    # Fix round 1 (the round-1 verify's M38): an escaped ``$`` starts no ``$'…'``.
    assert _both("echo \"${PWD%\\$'}abc'}\"") == (Verdict.ALLOW, Verdict.ALLOW)


# --- #788: ``$[…]`` and ``(( … ))`` in the shells that lack them -----------------------

# Fix round 1. The owner's decision (2026-11-12): refuse protected targets
# and keep ``(( n > 0 ))`` allowed. The lead's reading of it, after the
# round-1 cross-review showed dash runs any ``(( … ))`` as commands: dash's
# reading counts only when it is DENY, under the POSIX dialect. Classified
# only; ALLOW on 52bc607a and a9f39aa2 but the last two (ASK there).
# ``echo $[2>~/.bashrc]`` writes ``~/.bashrc]`` in dash, ksh93 and mksh; the
# reading takes the ``]`` off that word too.
_ARITHMETIC_DENIED = (
    "(( rm -rf /home ))",
    "((rm -rf /home))",
    "(( a || rm -rf /home ))",
    "(( 1 & rm -rf /home ))",
    "ls && (( rm -rf /home ))",
    "(( find / -delete ))",
    "(( ls | sh ))",
    "echo $[1|rm -rf /home ]",
    "echo $[1&rm -rf /home ]",
    "echo $[ 2 | rm -rf ~ ]",
    "echo $(echo $[1|sh])",
    "(( 1>'/etc/x' ))",
    "echo $[2>'/etc/x']",
    "echo $[2>~/.bashrc]",
    "(( 1>~/.bashrc ))",  # tree-sitter rejects it; the reading runs on that tree
    "(( 1>~/.ssh/authorized_keys ))",
)


@pytest.mark.parametrize("command", _ARITHMETIC_DENIED)
def test_dashs_reading_is_refused_under_posix(command: str) -> None:
    unknown, posix = _both(command)
    assert posix == Verdict.DENY, command
    assert unknown in (Verdict.ALLOW, Verdict.ASK), command  # the bash reading stands


# ALLOW on 52bc607a and on this tree, under both dialects (ASK under POSIX on
# a9f39aa2). Ordinary arithmetic, the verify's 102 real ``(( … ))`` commands
# among them (``.omc/probes/784-788/verify-r1/cost/extracted.txt``). The
# residuals the owner's decision keeps: dash writes ``0``, ``MARK``,
# ``MARK]`` and ``=`` in the next five (macOS and Linux dash; ``MARK]`` in
# both ksh93 and mksh too), and the ``(( echo == hi ))`` row printed
# ``== hi`` in both dash builds.
_ARITHMETIC_STAYS = (
    "(( n > 0 ))",
    "(( 1>MARK ))",
    "echo $[2>MARK]",
    "(( x >= MARK ))",
    "echo $[2>MARK]1",
    "(( echo == hi ))",
    "(( a > b )) && echo yes",
    "(( $# < 2 ))",
    "(( x << 2 ))",
    "(( x < y ))",
    "(( 2 > 1 )) && echo yes",
    "(( ${#arr[@]} > 0 ))",
    "((i < ${#COMP_WORDS[@]} - 1))",
    "((BASH_VERSINFO[0] >= 5 || BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3))",
    "(( opt_device || opt_mount || opt_file ))",
    "for (( i = 0; i < 3; i++ )); do echo $i; done",
    "echo $(( 1>MARK ))",
    "echo $((a > b))",
    'echo "$[2>MARK]"',
    "echo $[1+2]",
    "echo $[2<MARK]",
    "{ echo $((a > b)); }",
)


@pytest.mark.parametrize("command", _ARITHMETIC_STAYS)
def test_ordinary_arithmetic_stays_allowed(command: str) -> None:
    expected = Verdict.ASK if command.startswith("for") else Verdict.ALLOW
    assert _both(command) == (expected, expected), command


def test_the_arithmetic_reading_is_dashs() -> None:
    source = b"(( 1>MARK )); echo $[2>MARK]; echo $((1))"
    edits = bc._dash_edits(_parse(source))
    assert bc._edited(source, edits) == b"( ( 1>MARK ) ); echo \\$[2>MARK ]; echo $((1))"
    nested = b"(( ((1>2)) ))"
    assert bc._edited(nested, bc._dash_edits(_parse(nested))) == b"( ( ( ( 1>2 ) ) ) )"
    rejected = b"(( 1>~/.bashrc ))"
    assert bc._edited(rejected, bc._dash_edits(_parse(rejected))) == b"( ( 1>~/.bashrc  ) )"
    assert bc._dash_edits(_parse(b"for (( i = 0; i < 3; i++ )); do :; done")) == []
    assert bc._dash_edits(_parse(b"{ echo $((a > b)); }")) == []


# --- #789: a ``<(…)`` after an escaped ``}`` in a quoted ``${…}`` ----------------------

# ALLOW on 52bc607a and a9f39aa2. tree-sitter ends the ``${…}`` at the
# escaped ``}``; bash reads the operand on and runs the ``<(…)``. Each created
# MARK in Linux bash 5.2.37, and the first 13 in macOS bash 3.2.57 too
# (``.omc/probes/784-788/impl/audit-rerun/clusters.txt``).
_CUT_OPERAND_RUNS = (
    'echo "${PWD#\\}<(: > MARK)}"',
    'echo "${PWD##\\}<(: > MARK)}"',
    'echo "${PWD%\\}>(: > MARK)}"',
    'echo "${PWD#\\}>(: > MARK)}"',
    'echo a"${PWD#\\}>(: > MARK)}"',
    'echo a"${PWD#\\}<(: > MARK)}"',
    'echo a"${PWD%\\}>(: > MARK)}"',
    'echo "${PWD%%[\\}]a<(: > MARK)}"',
    'echo "${PWD##[\\}]<(: > MARK)}"',
    'echo "${PWD%[\\}]a>(: > MARK)}"',
    'cat "${PWD#[\\}]<(: > MARK)}"',
    'echo "${PWD%[\\}]a<(: > MARK)}"',
    'echo "${PWD#time $\\)\\}>(: > MARK)}"',
    # Linux bash 5.2.37 alone
    'echo "${PWD^^\\}<(: > MARK)}"',
    'echo a"${PWD/%a/[\\}]>(: > MARK)}"',
    'echo "${PWD//a/[\\}]<(: > MARK)}"',
    'echo "${PWD/#a/[\\}]<(: > MARK)}"',
    'cat "${PWD/a/[\\}]a<(: > MARK)}"',
    'cat "${PWD/%a/[\\}]>(: > MARK)}"',
    'echo a"${PWD,,[\\}]a<(: > MARK)}"',
    'echo "${PWD,,[\\}]>(: > MARK)}"',
    'echo "${PWD,,\\}<(: > MARK)}"',
    'echo "${PWD,\\}>(: > MARK)}"',
    'echo a"${PWD/%a/[\\}]a>(: > MARK)}"',
    'echo a"${PWD/a/[\\}]a<(: > MARK)}"',
    'echo a"${PWD,,[\\}]<(: > MARK)}"',
    'cat "${PWD,[\\}]<(: > MARK)}"',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _CUT_OPERAND_RUNS)
def test_a_substitution_after_an_escaped_brace_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


def test_a_substitution_after_an_escaped_brace_in_a_here_document() -> None:
    # Created MARK in macOS bash 3.2.57 and Linux bash 5.2.37 (the round-1 verify).
    assert _both("cat <<E\n${PWD#\\}<(: > MARK)}\nE") == (Verdict.ASK, Verdict.ASK)
    assert _both("cat <<E\n${PWD#\\}<(rm -rf ~)}\nE") == (Verdict.DENY, Verdict.DENY)
    # A quoted delimiter expands nothing.
    assert _both('cat <<"E"\n${PWD#\\}<(rm -rf ~)}\nE') == (Verdict.ALLOW, Verdict.ALLOW)


# Fix round 2 (the round-2 verify's F1): bash reads the operand on past a
# quoted ``}``, a ``"…"`` nested in it included, which tree-sitter reads as
# the end of the string. ALLOW on 52bc607a and f5d61467; each created MARK in
# macOS bash 3.2.57 and Linux bash 5.2.37, the last two in Linux bash alone
# (``.omc/probes/784-788/verify-r2/ev/g4-cut.jsonl``,
# ``.omc/probes/784-788/fix-r2/ev/f1-sib-onf5.txt``).
_CUT_OPERAND_QUOTED_RUNS = (
    "echo \"${PWD#\\}'}'<(: > MARK)}\"",
    'echo "${PWD#\\}"}"<(: > MARK)}"',
    "echo \"${PWD%%[\\}]'}'<(: > MARK)}\"",
    'echo "${PWD#\\}$"}"<(: > MARK)}"',
    'echo "${PWD#\\}"a"<(: > MARK)}"',
    'echo "${PWD#\\}"\'}\'"<(: > MARK)}"',
    'echo "${PWD#\\}"${y:-}}"<(: > MARK)}"',
    'echo "${PWD#\\}\'}\'"}"<(: > MARK)}"',
    'cat <<< "${PWD#\\}"}"<(: > MARK)}"',
    "[ -n \"${PWD#\\}'}'<(: > MARK)}\" ]",
    'echo $"${PWD#\\}"}"<(: > MARK)}"',
    'cat <<E\n${PWD#\\}"}"<(: > MARK)}\nE',
    "echo \"${PWD^^\\}'}'<(: > MARK)}\"",
    'echo "${PWD/a/[\\}]"}"<(: > MARK)}"',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _CUT_OPERAND_QUOTED_RUNS)
def test_a_substitution_after_a_quoted_brace_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


# The finish (the round-3 verify's F2): a ``"…"`` nested in the operand
# holding a ``${…}`` whose operand holds a ``}``. ALLOW on 52bc607a and
# 5be3fc88; each created MARK in macOS bash 3.2.57 and Linux bash 5.2.37, as
# did the 32 more of the verify's 42 rows (``.omc/probes/784-788/verify-r3/
# ev/a5-nested.jsonl``), which ask too (``.omc/probes/784-788/finish/ev/``).
_CUT_OPERAND_NESTED_RUNS = (
    'echo "${PWD#\\}"${y:-"}"}"<(: > MARK)}"',
    'echo "${PWD#\\}"${y+"}"}"<(: > MARK)}"',
    'echo "${PWD#\\}"${y#"}"}"<(: > MARK)}"',
    'echo "${PWD#\\}"${y/"}"/}"<(: > MARK)}"',
    'echo "${PWD#\\}"${y:-"}a"}"<(: > MARK)}"',
    'echo "${PWD#\\}"${y:-${z:-"}"}}"<(: > MARK)}"',
    'echo "${PWD#\\}${y:-"${z:-"}"}"}<(: > MARK)}"',
    'echo "${PWD%%[\\}]"${y%"}"}"<(: > MARK)}"',
    'cat <<E\n${PWD#\\}"${y:-"}"}"<(: > MARK)}\nE',
    'cat <<E\n${PWD%%[\\}]"${y:+"}"}"<(: > MARK)}\nE',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _CUT_OPERAND_NESTED_RUNS)
def test_a_substitution_after_a_nested_expansion_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


# ALLOW on 52bc607a and still ALLOW: no substitution after the cut, or one
# past the ``}`` that ends the operand, which is text in double quotes. Fix
# round 2: of the last three, a ``'\'`` holds a backslash, ``"}"x}`` ends at
# the ``}`` after ``x``, and an escaped ``\<(`` is text (the round-2 verify's
# F5, mutant R57); neither bash ran the ``<(…)`` of their ``: > MARK`` twins.
_CUT_OPERAND_STAYS = (
    'echo "${PWD#\\}}"',
    'echo "${PWD#\\}abc}"',
    'echo "${x#\\}}<(a)"',
    'echo "${PWD%\\}${y}}"',
    'echo "${x}<(a)"',
    "echo \"${PWD#\\}'\\'}'<(rm -rf ~)}\"",
    'echo "${PWD#\\}"}"x}<(rm -rf ~)"',
    'echo "${PWD#\\}\\<(rm -rf ~)}"',
    # The finish: past the nested ``${…}``, ``x}`` ends the operand, and an
    # escaped ``\$`` starts none; neither bash ran these twins' ``<(…)``.
    'echo "${PWD#\\}"${y:-"}"}"x}<(rm -rf ~)"',
    'echo "${PWD#\\}"\\${y:-"}"}"<(rm -rf ~)}"',
    # A ``'`` in a nested ``"…"`` is text, and ``"'"`` ends there (mutant F2c
    # of the finish read it as a quote); both bash printed the twin's ``<(…)``.
    'cat <<E\n${PWD#\\}"\'"}<(rm -rf ~)}\nE',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _CUT_OPERAND_STAYS)
def test_text_after_an_escaped_brace_stays_allowed(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


def test_the_operand_ends_where_bash_ends_it() -> None:
    assert bc._operand_end(b"a${b}c$(d})e}f}", 0, 15) == 12
    assert bc._operand_end(b"a\\}b}", 0, 5) == 4
    assert bc._operand_end(b"abc", 0, 3) == 3
    # Fix round 2: past a quoted ``}``, and no further than ``stop``.
    quoted = b"'}'\"}\"$'\\'}'`}`}"
    assert bc._operand_end(quoted, 0, len(quoted)) == len(quoted) - 1
    assert bc._operand_end(b"'\\'}", 0, 4) == 3
    assert bc._operand_end(b"ab}", 0, 2) == 2
    # Fix round 2 (F3): scans share where they end; a ``${…}`` is a scan of its own.
    ends: dict[int, int] = {}
    assert bc._operand_end(b"a${b}c}d}", 0, 9, ends) == 6
    assert ends[0] == ends[1] == ends[5] == 6 and ends[3] == ends[4] == 4
    assert bc._operand_end(b"a${b}c}d}", 3, 9, ends) == 4
    # The finish (F2): past a ``${…}`` in a nested ``"…"``, as bash ends it.
    assert bc._operand_end(b'"${y:-"}"}"<(a)}', 0, 16) == 15
    assert bc._operand_end(b'"${y:-${z:-"}"}}"<(a)}', 0, 22) == 21
    assert bc._operand_end(b'"\\${y:-"}"}', 0, 11) == 8
    assert bc._nested_quote_end(b'a${b:-"}"}c"', 0, 12) == 11
    assert bc._nested_quote_end(b"a${b", 0, 4) == 4
    assert bc._nested_quote_end(b"a\\", 0, 2) == 2  # ``stop``, not past it


# --- Bounds -----------------------------------------------------------------------------


def test_each_reading_stays_bounded() -> None:
    """Medians of 7 on macOS, Python 3.12 (``.omc/probes/784-788/impl/perf/``, ``fix-r1/perf/``); the bound leaves room for a slow runner."""

    # 200 deep: at 400, Python 3.11 runs out of stack on every tree and asks.
    deep = "(( " + "(" * 200 + "1>2" + ")" * 200 + " ))"
    cases = (
        ("; ".join(["echo ${x#{};: > MARK}"] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        ("; ".join(["echo ${x#a{b}c}"] * 2000), Verdict.ALLOW, Dialect.UNKNOWN),
        ("; ".join(["[ x =~ >MARK ]"] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        ("; ".join(["echo ${x:-=(: > MARK)}"] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        ("; ".join(["echo \"${PWD%$'}\\x24(: > MARK)'}\""] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        ("echo " + "$[" * 3000 + "2>MARK" + "]" * 3000, Verdict.ASK, Dialect.POSIX),
        ("; ".join(["(( n > 0 ))"] * 2000), Verdict.ALLOW, Dialect.POSIX),
        ("\n".join([deep] * 16), Verdict.ALLOW, Dialect.POSIX),
        ("; ".join(['echo "${PWD#\\}<(: > MARK)}"'] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        ("; ".join(["[ x =~ a|b ]"] * 2000), Verdict.ASK, Dialect.UNKNOWN),
        # 24.7 s on the fix round's step 3, which looked behind every byte.
        ("echo ${x:-{a," + "\\\\" * 20000 + "=(a)}}", Verdict.ALLOW, Dialect.POSIX),
        # Fix round 2 (the round-2 verify's F3): 12.2 s on f5d61467, whose
        # every cut operand scanned to the end of the string.
        ('echo "' + "${a#\\}" * 8000 + '"', Verdict.ALLOW, Dialect.POSIX),
        ("echo " + '"${a#\\}" ' * 8000, Verdict.ALLOW, Dialect.UNKNOWN),
    )
    for command, verdict, dialect in cases:
        started = time.perf_counter()
        assert classify(command, dialect=dialect) == verdict, command[:60]
        assert time.perf_counter() - started < 5.0, command[:60]


# --- Mutant pins (the round-1 verify's F5 and the cross-review's finding 4) -----------


def test_rows_that_tell_the_round_1_survivors_apart() -> None:
    # M19 (``<`` dropped from the leaf scan): a here-document start in the leaf.
    assert _both("[ x =~ a<<E ]") == (Verdict.ASK, Verdict.ASK)
    # M39/M40 and the cross-review's M9 (the word before the first child
    # lost): zsh created MARK for the first (macOS and Linux zsh 5.9).
    assert _both("echo ${x:-=(: > MARK)$y}") == (Verdict.ASK, Verdict.ASK)
    assert _both("echo ${x:-=(rm -rf ~)$y}") == (Verdict.DENY, Verdict.DENY)
    # M41 (``=(`` widened to ``=``): ``=<(…)`` is a parsed substitution.
    assert _both("echo ${x:-=<(rm -rf ~)}") == (Verdict.DENY, Verdict.DENY)
    # M38: see test_a_split_dollar_quote_asks_as_an_unsplit_one_does.


def test_rows_that_tell_this_rounds_survivors_apart() -> None:
    """Rows the fix round's own mutants changed (``.omc/probes/784-788/fix-r1/mutants/``)."""

    # An ``=(`` after a ``,`` outside braces is no zsh substitution: zsh ran
    # the first ``=(`` of ``${x-=(: > MARK),=(: > MARK2)=}`` and not the
    # second (macOS and Linux zsh 5.9 left ``MARK`` alone).
    assert _both("echo ${x-=(: > MARK),=(rm -rf ~)=}") == (Verdict.ASK, Verdict.ASK)
    # The tree-sitter text is read beside zsh's (DENY on 52bc607a).
    assert _both("echo ${x/a/<(rm -rf ~)a(<(rm -rf ~){=(: > MARK)}") == (
        Verdict.DENY,
        Verdict.DENY,
    )
    # dash's reading spaces only the parentheses of a ``((``: none of the
    # nine shells ran this line's ``: > MARK`` twin, a syntax error to each.
    assert _both("echo ${x:-$(rm -rf ~)},((,}") == (Verdict.ASK, Verdict.ASK)
    # A ``>`` in an ``extglob_pattern`` leaf: the seven shells other than zsh
    # created MARK (ALLOW on 52bc607a).
    assert _both('[ "$x" == *\\(>MARK ]') == (Verdict.ASK, Verdict.ASK)
    # An escaped ``{`` opens no brace group, and an escaped ``,`` splits none;
    # zsh ran neither ``: > MARK`` twin (macOS and Linux zsh 5.9).
    assert _both("echo ${x:-\\{a,=(rm -rf ~)}}") == (Verdict.ASK, Verdict.ASK)
    assert _both("echo ${x:-{a\\,=(rm -rf ~)}}") == (Verdict.ALLOW, Verdict.ALLOW)
    # #784's edit is read with 52bc607a's, not without: ``<}\}}`` reads a
    # file, and no shell wrote one (ALLOW on 52bc607a).
    assert _both("echo ${x/%a{b}a<}\\}}") == (Verdict.ALLOW, Verdict.ALLOW)


def test_rows_that_tell_the_round_2_survivors_apart() -> None:
    """Rows fix round 2's own mutants changed (``.omc/probes/784-788/fix-r2/mutants/``)."""

    # The ``)`` closes the substitution, which is read on its own, not with
    # the pattern text after it: both bash created MARK for the twin
    # ``echo "${PWD#\\}<(: > MARK)echo a}"``.
    assert _both('echo "${PWD#\\}<(rm -rf ~)echo a}"') == (Verdict.DENY, Verdict.DENY)
    # A here-document's operand ends with its body: both bash printed
    # ``<(: > MARK)`` for the twin and created nothing.
    assert _both("cat <<E\n${PWD#\\}\nE\necho '<(rm -rf ~)'") == (Verdict.ALLOW, Verdict.ALLOW)
    # A ``<(`` before the cut is text in double quotes, and only what follows
    # a cut is read: neither bash ran the twin's ``: > MARK``.
    assert _both('echo "<(rm -rf ~)${PWD#\\}}"') == (Verdict.ALLOW, Verdict.ALLOW)
    # Every cut is read, not only the first: both bash created MARK.
    assert _both('echo "${PWD#\\}}" "${PWD#\\}<(: > MARK)}"') == (Verdict.ASK, Verdict.ASK)
    assert _both('echo "${PWD#\\}}" "${PWD#\\}<(rm -rf ~)}"') == (Verdict.DENY, Verdict.DENY)


def test_a_row_that_tells_the_round_3_verifys_m39_apart() -> None:
    """The round-3 verify's F4: M39 kept the ``(`` a ``)`` closes open (``opens[-1]`` for ``opens.pop()``)."""

    # The inner ``((`` is closed first, so the substitution's own ``)`` is
    # found and only its command is read: both bash created MARK for the
    # twin ``echo "${PWD#\\}<((: > MARK)) x}"``
    # (``.omc/probes/784-788/verify-r3/ev/survivor-rows-shells.txt``).
    assert _both('echo "${PWD#\\}<((rm -rf ~)) x}"') == (Verdict.DENY, Verdict.DENY)


# --- Real shells -------------------------------------------------------------------

_PATH_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


def _shell_or_skip(name: str) -> str:
    if sys.platform == "win32":
        pytest.skip("POSIX shells")
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"no {name}")
    return path


def _created(shell: str, command: str, work: Path) -> list[str]:
    """Run ``command`` in an empty ``work``; the names of the files it left there."""

    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [shell, "-c", command],
        cwd=work,
        env=_PATH_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return sorted(path.name for path in work.iterdir())


def _is(path: str, name: str) -> bool:
    return Path(path).resolve().name == name or Path(path).name == name


@pytest.mark.parametrize("shell", ["bash", "dash", "mksh"])
def test_the_shells_end_the_expansion_at_the_first_brace(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    if shell == "bash" and not _is(path, "bash"):
        pytest.skip("bash is not bash")
    assert _created(path, "echo ${x#{};: > MARK}", tmp_path) == ["MARK}"]
    assert classify("echo ${x#{};: > MARK}") == Verdict.ASK


@pytest.mark.parametrize("shell", ["bash", "dash", "ksh", "mksh"])
def test_the_shells_redirect_in_a_bracket_pattern(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    assert _created(path, "[ x =~ >MARK ]", tmp_path) == ["MARK"]
    assert classify("[ x =~ >MARK ]") == Verdict.ASK


@pytest.mark.parametrize("shell", ["bash", "ksh", "mksh"])
def test_the_shells_run_a_list_in_a_bracket_pattern(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    if _is(path, "zsh") or _is(path, "dash"):
        pytest.skip(f"{shell} is zsh or dash here")
    for at, command in enumerate(_BRACKET_LIST_RUNS):
        assert _created(path, command, tmp_path / str(at)) == ["MARK"], command
        assert classify(command, dialect=Dialect.POSIX) == Verdict.ASK, command


def test_dash_redirects_in_a_double_bracket_pattern(tmp_path: Path) -> None:
    dash = _shell_or_skip("dash")
    assert _created(dash, "[[ x =~ >MARK ]]", tmp_path) == ["MARK"]
    assert classify("[[ x =~ >MARK ]]", dialect=Dialect.POSIX) == Verdict.ASK


def test_zsh_runs_an_operand_substitution(tmp_path: Path) -> None:
    zsh = _shell_or_skip("zsh")
    assert _created(zsh, "echo ${x:-=(: > MARK)}", tmp_path) == ["MARK"]
    assert _created(zsh, 'echo "${x:-=(: > MARK)}"', tmp_path / "quoted") == []
    assert _created(zsh, "echo ${x:-{a,=(: > MARK)}}", tmp_path / "brace") == ["MARK"]
    assert classify("echo ${x:-=(: > MARK)}") == Verdict.ASK
    assert classify("echo ${x:-{a,=(: > MARK)}}") == Verdict.ASK


def test_bash_3_runs_a_split_dollar_quote(tmp_path: Path) -> None:
    """macOS's ``/bin/bash`` 3.2 runs it; Linux bash 5.2.37 did not."""

    bash = _shell_or_skip("bash")
    version = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, check=False, timeout=60
    )
    if version.stdout.strip() != b"3":
        pytest.skip("bash 3.2 decodes and expands the split $'...' again")
    assert _created(bash, _SPLIT_RUNS[0], tmp_path) == ["MARK"]
    assert classify(_SPLIT_RUNS[0]) == Verdict.ASK


def test_bash_runs_a_substitution_after_an_escaped_brace(tmp_path: Path) -> None:
    bash = _shell_or_skip("bash")
    if not _is(bash, "bash"):
        pytest.skip("bash is not bash")
    assert _created(bash, _CUT_OPERAND_RUNS[0], tmp_path) == ["MARK"]
    assert classify(_CUT_OPERAND_RUNS[0], dialect=Dialect.POSIX) == Verdict.ASK


@pytest.mark.parametrize("shell", ["dash", "ksh", "mksh"])
def test_a_shell_with_no_dollar_bracket_redirects(shell: str, tmp_path: Path) -> None:
    """The residual the owner's decision keeps: a write that is not to a protected path stays allowed."""

    path = _shell_or_skip(shell)
    if _is(path, "bash") or _is(path, "zsh"):
        pytest.skip(f"{shell} is bash or zsh here")
    assert _created(path, "echo $[2>MARK]", tmp_path) == ["MARK]"]
    assert classify("echo $[2>MARK]", dialect=Dialect.POSIX) == Verdict.ALLOW
    assert classify("echo $[2>~/.bashrc]", dialect=Dialect.POSIX) == Verdict.DENY


def test_dash_runs_double_parentheses_as_subshells(tmp_path: Path) -> None:
    """The residual the owner's decision keeps: ``(( n > 0 ))`` writes ``0`` in dash and stays allowed."""

    dash = _shell_or_skip("dash")
    assert _created(dash, "(( n > 0 ))", tmp_path) == ["0"]
    assert classify("(( n > 0 ))", dialect=Dialect.POSIX) == Verdict.ALLOW
    assert classify("(( 1>~/.bashrc ))", dialect=Dialect.POSIX) == Verdict.DENY


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_bash_and_zsh_read_both_as_arithmetic(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    assert _created(path, "echo $[2>MARK]; (( 1>MARK ))", tmp_path) == []


# --- The entry points that act on the verdict ----------------------------------------


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


# Every classifiable shell is read under the POSIX dialect at the gate.
_GATE_ASK = (
    "echo ${x#{};: > MARK}",
    "[ x =~ >MARK ]",
    "[ x =~ a;{sh,-c,:\\>MARK};b ]",
    "echo ${x:-=(: > MARK)}",
    "echo ${x:-{a,=(: > MARK)}}",
    _SPLIT_RUNS[0],
    _CUT_OPERAND_RUNS[0],
    # The finish: the round-3 verify's F1, F2 and F3 rows, which the RPC
    # evaluation allowed for each shell on 5be3fc88.
    _TEST_AND_RUNS[0],
    _CUT_OPERAND_NESTED_RUNS[0],
    _ZSH_TEXT_EMPTY_RUNS[0],
)
_GATE_DENY = (
    "echo ${x#{};rm -rf ~ }",
    "[ x =~ >~/.bashrc ]",
    "echo ${x:-=(rm -rf ~)}",
    'echo ${PWD#"$ $(rm -rf ~)"{}}',
    "(( rm -rf /home ))",
    "(( 1>~/.bashrc ))",
    "echo $[1|rm -rf /home ]",
    'echo "${PWD#\\}<(rm -rf ~)}"',
    _TEST_AND_RUNS[0].replace("mkdir -p MARK", "rm -rf /home"),
    _CUT_OPERAND_NESTED_RUNS[0].replace(": > MARK", "rm -rf ~"),
    _ZSH_TEXT_EMPTY_RUNS[0].replace(": > MARK", "rm -rf ~"),
)
_GATE_READS = (
    "echo ${x#a{b}c}",
    "[[ $x =~ ^a ]]",
    "echo $((a > b))",
    "(( 2 > 1 )) && echo yes",
    "(( n > 0 ))",
    "echo $[2>MARK]",
    '[ -n "$a" && -n "$b" ]',
)


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/dash"])
async def test_auto_interactive_asks(shell: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_shell(monkeypatch, shell)
    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    for command in _GATE_ASK:
        ctx = _Ctx(has_ui=True)
        result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert ctx.ui.select_calls == 1, command
        assert isinstance(result, ToolCallResult) and result.block, command  # the UI said No
    for command in _GATE_DENY:
        ctx = _Ctx(has_ui=True)
        result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert ctx.ui.select_calls == 0, command
        assert isinstance(result, ToolCallResult) and result.block, command
    for command in _GATE_READS:
        ctx = _Ctx(has_ui=True)
        assert await perm._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]
        assert ctx.ui.select_calls == 0, command


async def test_auto_headless_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``headless_default="block"`` blocks the ASK; ``-p`` (``"allow"``) runs it, as every ASK (#740)."""

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
    for command in _GATE_DENY:
        denied = await allow._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(denied, ToolCallResult) and denied.block, command
    for command in _GATE_READS:
        assert await block._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/dash"])
def test_rpc_permissions_evaluate_matches_the_gate(
    shell: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_shell(monkeypatch, shell)
    for commands, expected in ((_GATE_ASK, "ask"), (_GATE_DENY, "deny"), (_GATE_READS, "allow")):
        for command in commands:
            got = evaluate_tool_call(PermissionMode.AUTO, "bash", {"command": command}, "/proj")
            assert got == expected, command


# --- Only raises: rows a9f39aa2 lowered, and its new readings never below 52bc607a -----


def test_no_listed_row_is_below_its_52bc607a_verdict() -> None:
    """Each pair is the row's verdict on 52bc607a (UNKNOWN, POSIX); this tree's is the same or higher."""

    rows = (
        ('echo ${PWD#"$ $(rm -rf ~)"{}}', (Verdict.DENY, Verdict.DENY)),
        ("[ x =~ '>'<] ]", (Verdict.ASK, Verdict.ASK)),
        ("(( 1>~/.bashrc ))", (Verdict.ASK, Verdict.ASK)),
        ("echo $[2>~/.bashrc ]", (Verdict.ASK, Verdict.ASK)),
        ("[ a || : > MARK ]", (Verdict.ASK, Verdict.ASK)),
    )
    for command, before in rows:
        for got, was in zip(_both(command), before, strict=True):
            assert _ORDER[got] >= _ORDER[was], command
