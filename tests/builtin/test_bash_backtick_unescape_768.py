"""#768 — auto reads backtick text as the shells read it, after they undo ``\\\\``, ``\\$``, ``\\``` and ``\\⏎``.

Before the shells parse the text inside backticks again, they turn ``\\\\``
into ``\\``, ``\\$`` into ``$`` and ``\\``` into a backtick, and remove
``\\⏎``. tree-sitter-bash 0.25.1 reads backtick text as it stands, so on
363accf2 ``echo `echo "${x:-'\\$(: > MARK)'}"``` was ALLOW while all nine
shells measured ran its ``$(…)``: unescaped, the ``'…'`` sits in a ``${…}``
operand inside double quotes, where it does not quote (#742). The same
unescaping turned ``echo `find . '-de\\⏎lete'``` (ALLOW) into a ``find``
given ``-delete``.

Every shell row was measured with ``<shell> -c`` in a scratch directory holding
``files/z[$(: > MARK)]``: macOS bash 3.2.57, zsh 5.9, dash and ksh93u+
2012-08-01, and Linux (docker ``python:3.12-slim``) bash 5.2.37, zsh 5.9, dash
0.5.12, ksh 93u+m/1.0.10 and mksh 59c (``.omc/probes/768/impl/``). The comment
on a row names the shells that created MARK. The ``rm -rf ~`` and
``find -delete`` rows were only classified, never run.

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
_BN = "\\\n"  # a backslash-newline
_BBN = "\\\\\n"  # two backslashes and a newline: ``\\⏎`` once the backticks are undone

# Inside the backticks, before the shells unescape it; each ran in the shells
# named, in each of the three places below (``.omc/probes/768/impl/``, the
# grid). Every one of the 18 was ALLOW on 363accf2.
_INNER_RUNS = (
    "echo \"${x:-'\\$(: > MARK)'}\"",  # all nine
    "echo \"${x:-'$" + _BBN + "(: > MARK)'}\"",  # macOS dash, ksh93u+; Linux dash, mksh
    "echo \"${x:-'$" + _BBN + _BBN + "(: > MARK)'}\"",  # the same four
    "echo \"${x-'$" + _BBN + "(: > MARK)'}\"",  # the same four
    "echo \"${x:-$'$" + _BBN + "(: > MARK)'}\"",  # macOS dash, ksh93u+; Linux dash
    'echo "\\$(: > MARK)"',  # all nine
)
_PLACES = ("echo `{}`", "echo $(echo `{}`)", "ls | grep `{}`")
_RUNS = tuple(place.format(inner) for place in _PLACES for inner in _INNER_RUNS)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _RUNS)
def test_backtick_text_the_shells_unescape_into_code_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _RUNS)
def test_its_rm_twin_is_refused(command: str, dialect: Dialect) -> None:
    twin = command.replace(": > MARK", "rm -rf ~")
    assert classify(twin, dialect=dialect) == Verdict.DENY, twin


# Classified only. ALLOW on 363accf2 unless noted. ``printf '[%s]\n' `printf
# %s '-de\⏎lete'``` printed ``[-delete]`` in all nine shells.
_DENIED = (
    "echo `find . '-de" + _BN + "lete'`",
    "echo \"`find . '-de" + _BN + "lete'`\"",  # ASK on 363accf2
    # tree-sitter rejects ``\$(`` here, so the command asked before any
    # reading on 363accf2; the ``: > MARK`` twin ran in all nine shells
    "echo `echo \\$(rm -rf ~)`",
    "v=`x=\\$(rm -rf ~)`",
    "echo $(echo `echo \\$(rm -rf ~)`)",
    # ASK on 363accf2; the twin ran in seven shells (not the two ksh93)
    "echo `echo $" + _BBN + "(rm -rf ~)`",
    # in double quotes or a here-document body, with ``\"`` undone (ASK on 363accf2)
    'echo "`echo \\"\\$(rm -rf ~)\\"`"',
    'cat <<E\n`echo \\"\\$(rm -rf ~)\\"`\nE',
    # the ``\"`` reading of backticks in double quotes; its ``: > MARK``
    # twin ran in all nine (ASK there and now). The second is rejected by
    # tree-sitter (the stray ``)``), so only the reading of a rejected
    # command's backticks sees it (ASK on 363accf2)
    'echo "`echo \\"${x:-\'\\$(rm -rf ~)\'}\\"`"',
    'echo "`echo \\"${x:-\'\\$(rm -rf ~)\'}\\"`" )',
    # in a ``${…}`` operand tree-sitter leaves as a word, and in a
    # here-document body it leaves as text; ASK on 363accf2, the twins ran in
    # seven shells (not the two ksh93) and in all nine
    "echo ${y:-`echo $" + _BBN + "(rm -rf ~)`}",
    'echo "${y:-`echo $' + _BBN + '(rm -rf ~)`}"',
    "cat <<E\n`echo \\$(rm -rf ~)`\nE",
    "cat <<E\n`x=\\$(rm -rf ~)`\nE",
    # ``$(…)`` in a here-document body joins ``\⏎`` in seven of the nine
    # shells (dash keeps it); ASK on 363accf2
    "cat <<E\n$(find . '-de" + _BN + "lete')\nE",
    "cat <<E\n${x:-$(find . '-de" + _BN + "lete')}\nE",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _DENIED)
def test_what_the_shells_read_in_backticks_keeps_its_deny(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


# Unchanged from 363accf2. None of the nine shells ran a MARK twin of the
# ALLOW rows that hold one.
_KEPT = (
    ("echo `echo \\$HOME`", Verdict.ALLOW),
    ("echo `echo \\${HOME}`", Verdict.ALLOW),
    ("echo `echo \\\\\\$HOME`", Verdict.ALLOW),
    ("echo `date`", Verdict.ALLOW),
    ("echo `git rev-parse HEAD`", Verdict.ALLOW),
    ("echo `ls" + _BN + " -la`", Verdict.ALLOW),
    ("echo `echo '\\$(: > MARK)'`", Verdict.ALLOW),
    ("echo `echo '$" + _BN + "(: > MARK)'`", Verdict.ALLOW),
    # ``$\`` is a literal ``$`` in all nine; the read text keeps ``\$`` there
    ("echo `echo \\$\\(ls\\)`", Verdict.ALLOW),
    ("ls | grep `echo '\\$(: > MARK)'`", Verdict.ALLOW),
    # top-level ``$(…)`` keeps ``\⏎`` in a ``'…'`` in all nine
    ("echo $(find . '-de" + _BN + "lete')", Verdict.ALLOW),
    ("echo `basename \\`pwd\\``", Verdict.ASK),
    ("x=`printf '%s' \"a\\\\b\"`", Verdict.ASK),
    ("echo `awk '{print \\$1}' file.txt`", Verdict.ASK),
    # in ``"$(…)"`` all nine keep ``\⏎`` in a ``'…'``
    ("echo \"$(find . '-de" + _BN + "lete')\"", Verdict.ASK),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(("command", "verdict"), _KEPT)
def test_ordinary_backticks_keep_their_verdict(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# tree-sitter-bash 0.25.1 reads two backtick substitutions a blank apart as one
# (``\`echo x\` \`touch MARK\``` with a ``\` \``` token inside), so the second
# read as arguments of the first's ``echo``. The shells end the first at its
# closing backtick. ALLOW on 363accf2; all nine shells ran ``touch MARK``.
_MERGED_RUNS = (
    "echo `echo x` `touch MARK`",
    "echo `echo x`  `touch MARK`",
    "echo `echo x`\t`touch MARK`",
    "ls `echo x` `touch MARK`",
    "echo `echo \\$x` `touch MARK`",
)
_MERGED_DENIED = (
    "echo `echo x` `rm -rf ~`",
    "echo `echo x`\t`rm -rf ~`",
    "echo `echo x` `echo y` `rm -rf ~`",
    "echo `ls` `find . -delete`",  # ASK on 363accf2
)
_MERGED_KEPT = (
    ("echo `echo x` `echo y`", Verdict.ALLOW),
    ("echo `echo x` `echo y` `echo z`", Verdict.ALLOW),
    ("echo `echo \\$x` `date`", Verdict.ALLOW),
    ("echo `date` `whoami`", Verdict.ASK),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(
    ("command", "verdict"),
    [(c, Verdict.ASK) for c in _MERGED_RUNS]
    + [(c, Verdict.DENY) for c in _MERGED_DENIED]
    + list(_MERGED_KEPT),
)
def test_backticks_a_blank_apart_are_read_as_two(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


def test_the_cost_a_nested_substitution_in_quotes_asks() -> None:
    """ALLOW on 363accf2, where ``"\\`pwd\\`"`` read as plain text.

    The shells run ``dirname "`pwd`"``, which the classifier asks on at the
    top level as well (a substitution in double quotes). The one string of the
    31,958 the cost list read whose verdict #768 changes.
    """

    assert classify('dirname "`pwd`"') == Verdict.ASK
    assert classify('echo `dirname "\\`pwd\\`"`') == Verdict.ASK


# --- The readings ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "plain", "quoted"),
    [
        (b"echo \\$HOME", b"echo $HOME", b"echo $HOME"),
        (b"a\\\\b \\` \\\nc", b"a\\b ` c", b"a\\b ` c"),
        (b'echo \\"x\\"', b'echo \\"x\\"', b'echo "x"'),
        # a ``$`` a backslash and a byte other than a newline follow stays escaped
        (b"echo \\$\\(ls\\)", b"echo \\$\\(ls\\)", b"echo \\$\\(ls\\)"),
        (b"\\$\\\\x", b"\\$\\x", b"\\$\\x"),
        # ``$\⏎`` is not literal: the next level joins it into ``$(``
        (b"\\$\\\\\n(ls)", b"$\\\n(ls)", b"$\\\n(ls)"),
        (b"\\$" + b"\\" + b"\\", b"$\\", b"$\\"),
        (b"\\\\\\$x", b"\\$x", b"\\$x"),
    ],
)
def test_what_the_shells_read_inside_backticks(text: bytes, plain: bytes, quoted: bytes) -> None:
    assert bc._unescaped(text, bc._BACKTICK_ESCAPE) == plain
    assert bc._unescaped(text, bc._BACKTICK_DQ_ESCAPE) == quoted


@pytest.mark.parametrize(
    ("command", "inside"),
    [
        ("echo `a` $(b)", ["`a`"]),
        ("cat <<E\n$(a)\nE", ["$(a)"]),
        ("cat <<E\n${x:-$(a)}\nE", ["$(a)"]),
        ("cat <<E\n$(a `b`)\nE", ["$(a `b`)"]),
        ('echo "$(a)" $(b)', []),
        ("cat <<'E'\n$(a)\nE", []),
    ],
)
def test_where_the_shells_join_every_continuation(command: str, inside: list[str]) -> None:
    source = command.encode()
    assert bc._PARSER is not None
    root = bc._PARSER.parse(source).root_node
    got = [source[lo:hi].decode() for lo, hi in bc._joining_substitutions(root)]
    assert got == inside


def test_ranges_inside_another_are_left_out() -> None:
    assert bc._outside([(0, 2), (3, 5), (6, 9), (12, 14)], [(3, 10), (11, 20)]) == [(0, 2)]
    assert bc._outside([(1, 4)], [(2, 9)]) == [(1, 4)]  # not wholly inside: kept
    assert bc._outside([(5, 12)], [(2, 9)]) == [(5, 12)]
    assert bc._outside([(1, 2)], []) == [(1, 2)]


def test_a_backtick_with_no_backslash_is_not_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []

    def spy(text: bytes, **_k: Any) -> Verdict:
        calls.append(text)
        return Verdict.ASK

    monkeypatch.setattr(bc, "_reread", spy)
    command = "echo `date`; echo `git rev-parse HEAD`; echo \"`pwd`\"; echo `printf 'a\\nb'`"
    assert classify(command) == Verdict.ASK
    assert classify("echo `date`") == Verdict.ALLOW
    assert calls == []


def test_the_quoted_reading_is_made_only_in_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []
    real = bc._reread

    def spy(text: bytes, **k: Any) -> Verdict:
        if k.get("command"):
            seen.append(text)
        return real(text, **k)

    monkeypatch.setattr(bc, "_reread", spy)
    classify('echo `echo \\"\\$x\\"`')
    assert seen == [b'echo \\"$x\\"']
    seen.clear()
    classify('echo "`echo \\"\\$x\\"`"')
    assert sorted(seen) == [b'echo "$x"', b'echo \\"$x\\"']


def test_the_nested_joined_reading_is_made_only_when_it_joins_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``'…'`` in backticks with no ``\\⏎`` in it leaves the nested reading equal to the joined one."""

    joined: list[bytes] = []
    real = bc._reread

    def spy(text: bytes, **k: Any) -> Verdict:
        if k.get("joined"):
            joined.append(text)
        return real(text, **k)

    monkeypatch.setattr(bc, "_reread", spy)
    assert classify("echo `echo 'ab'`; ls " + _BN + "-la") == Verdict.ALLOW
    assert joined == [b"echo `echo 'ab'`; ls -la"]
    joined.clear()
    assert classify("echo `echo 'a" + _BN + "b'`; ls " + _BN + "-la") == Verdict.ALLOW
    assert joined[:2] == [b"echo `echo 'ab'`; ls -la", b"echo `echo 'a\\\nb'`; ls -la"]


# --- Only raises: a budget of its own ----------------------------------------------


def test_the_backtick_readings_take_nothing_from_the_other_rereads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading that spends every byte paying it leaves #762's comment reading its DENY.

    The #762 reading of ``echo \\ #$(rm -rf ~)`` runs after the backticks are
    read; on one shared budget it would be left nothing.
    """

    real = bc._reread

    def greedy(text: bytes, **k: Any) -> Verdict:
        if k.get("command"):
            budget = bc._REREAD_BUDGET.get()
            assert budget is not None
            budget[0] = 0
        return real(text, **k)

    monkeypatch.setattr(bc, "_reread", greedy)
    command = "echo `echo \\$x`; echo \\ #$(rm -rf ~)"
    assert classify(command) == Verdict.DENY


def test_the_backtick_budget_is_paid_and_given_back() -> None:
    shared = [100]
    own = [7]
    token = bc._REREAD_BUDGET.set(shared)
    mine = bc._BACKTICK_BUDGET.set(own)
    try:
        assert (
            bc._on_backtick_budget(lambda: Verdict.DENY if bc._charge_reread(7) else Verdict.ALLOW)
            == Verdict.DENY
        )
        assert not bc._on_backtick_budget(
            lambda: Verdict.DENY if bc._charge_reread(1) else Verdict.ALLOW
        )
        assert bc._REREAD_BUDGET.get() is shared
    finally:
        bc._BACKTICK_BUDGET.reset(mine)
        bc._REREAD_BUDGET.reset(token)
    assert shared == [100]
    assert own == [0]


def test_a_backtick_reading_out_of_stack_never_lowers(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_text: bytes, **_k: Any) -> Verdict:
        raise RecursionError

    monkeypatch.setattr(bc, "_reread_command", boom)
    assert classify("find . -delete; echo `echo \\$x`") == Verdict.DENY
    assert classify("echo `echo \\$x`") == Verdict.ASK


def test_an_unpaid_backtick_reading_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    real = bc._on_backtick_budget

    def broke(read: Any) -> Verdict:
        own = bc._BACKTICK_BUDGET.get()
        if own is not None:
            own[0] = 0
        return real(read)

    monkeypatch.setattr(bc, "_on_backtick_budget", broke)
    assert classify("echo `echo \\$HOME`") == Verdict.ASK
    assert classify("echo `date`") == Verdict.ALLOW


def test_many_and_deep_backticks_stay_bounded() -> None:
    """Medians of 7 on macOS, Python 3.12 (``.omc/probes/768/impl/perf/``).

    2,000 ``echo `echo \\$x``` statements (34 KB): 55.9 ms, against 31.7 ms
    on 363accf2. A 12-deep nest of escaped backticks around ``find . -delete``
    (8 KB): 18.8 ms, against 1.5 ms. Every reading #768 adds is paid from its
    own budget, as large as the #742 one, so the bytes it re-reads stay within
    a fixed multiple of the command's; the work outside the re-reads grew with
    the square of some inputs until fix round 1 (the reading built in one pass,
    a split read in its statement: the tests above). The bound leaves room for
    a slow runner.
    """

    many = "; ".join(["echo `echo \\$x`"] * 2000)
    started = time.perf_counter()
    assert classify(many) == Verdict.ALLOW
    assert time.perf_counter() - started < 5.0
    inner = "find . -delete"
    for _ in range(12):
        inner = "echo `" + inner.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$") + "`"
    started = time.perf_counter()
    assert classify(inner) == Verdict.DENY
    assert time.perf_counter() - started < 5.0


# --- Fix round 1: #768's own findings ------------------------------------------------


def _reference_unescaped(text: bytes, escape: Any) -> bytes:
    """78df9ce4's ``_unescaped``, one insert per kept ``\\$``: the reading the one-pass build must equal."""

    out = bytearray()
    dollars: list[int] = []
    last = 0
    for match in escape.finditer(text):
        out += text[last : match.start()]
        if match.group(1) == b"$":
            dollars.append(len(out))
        out += match.group(1).replace(b"\n", b"")
        last = match.end()
    out += text[last:]
    for at in reversed(dollars):
        if out[at + 1 : at + 2] == b"\\" and out[at + 2 : at + 3] not in (b"\n", b""):
            out[at:at] = b"\\"
    return bytes(out)


def test_the_one_pass_reading_is_the_reading_it_replaces() -> None:
    """Fix round 1: ``\\$\\$\\x`` keeps both backslashes, as the inserts from the end did."""

    import random

    rng = random.Random(768)
    texts = [b"\\$\\$\\\\x", b"\\$\\$\\$\\\\\n", b"\\$\\\\\\$\\\\y", b"\\$" * 5 + b"\\\\z"]
    alphabet = [b"\\", b"$", b"`", b"\n", b'"', b"x", b"(", b" "]
    texts += [
        b"".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 24))) for _ in range(20000)
    ]
    for text in texts:
        for escape in (bc._BACKTICK_ESCAPE, bc._BACKTICK_DQ_ESCAPE):
            assert bc._unescaped(text, escape) == _reference_unescaped(text, escape), text


def test_the_reading_is_built_in_one_pass() -> None:
    """Fix round 1: 480,000 ``\\$\\a`` (1.9 MB) read in one pass.

    One insert per ``$`` took 1.9 s for 240,000 on 78df9ce4 (the round-1
    verify) and grows with the square; the bound leaves room for a slow runner.
    """

    text = b"echo " + b"\\$\\a" * 480000
    started = time.perf_counter()
    read = bc._unescaped(text, bc._BACKTICK_ESCAPE)
    assert time.perf_counter() - started < 3.0
    assert read.count(b"\\$\\a") == 480000


# Fix round 1: tree-sitter reads two substitutions a blank apart as one, and
# the reading that splits them read the whole source, every other line's pair
# still merged in it, until the budget ran out: five such lines asked on
# 78df9ce4 (the round-1 cross-review; ALLOW on 363accf2). The split is read in
# the statement that holds it now.
_SPLIT_SCRIPTS = (
    "echo `ls a` `ls b`\n" * 5,
    "\n".join(["ls -la", "git status", "echo hi"] * 20) + "\n" + "echo `ls a` `ls b`\n" * 3,
    "{\n" + " echo `ls a` `ls b`\n" * 5 + "}\n",
    "(\n" + "echo `ls a` `ls b`\n" * 5 + ")\n",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _SPLIT_SCRIPTS)
def test_lines_of_split_pairs_stay_allowed(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ALLOW, command


# Fix round 2 (the round-2 verify's F5): a list or pipeline member is a
# statement of its own too. Four or more such members joined by ``&&``, ``||``
# or ``|`` asked on 866b0e5b (ALLOW on 363accf2).
_SPLIT_LISTS = (
    " && ".join(["echo `ls a` `ls b`"] * 4),
    " || ".join(["echo `ls a` `ls b`"] * 5),
    " | ".join(["echo `ls a` `ls b`"] * 4),
    "echo `ls a` `ls b` && echo `ls a` `ls b` || echo `ls a` `ls b` | cat && echo `ls a` `ls b`",
    " && ".join(["! echo `ls a` `ls b`"] * 4),
)
_SPLIT_LISTS_DENIED = (
    "echo `echo x` `rm -rf ~` && ls && ls && ls",
    "ls && ls && ls && echo `echo x` `rm -rf ~`",
    "ls | ls | ls | echo `echo x` `rm -rf ~`",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(
    ("command", "verdict"),
    [(c, Verdict.ALLOW) for c in _SPLIT_LISTS] + [(c, Verdict.DENY) for c in _SPLIT_LISTS_DENIED],
)
def test_list_and_pipeline_members_split_on_their_own(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# Fix round 2 (the round-2 verify's F6): rows its mutants M15 and M16 (no
# ``case_item`` in ``_STATEMENT_PARENTS``: DENY → ASK) and M17 (no
# ``process_substitution``: ALLOW → ASK) changed and no test pinned. Both
# verdicts are 866b0e5b's.
_STATEMENT_KINDS = (
    ("case x in x) echo `echo x` `rm -rf ~`;; esac", Verdict.DENY),
    ("cat <(" + "echo `ls a` `ls b`\n" * 6 + ")", Verdict.ALLOW),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(("command", "verdict"), _STATEMENT_KINDS)
def test_a_case_item_and_a_process_substitution_hold_statements(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


def test_a_split_is_read_in_its_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    """No split reading holds more than the statement around the pair, so the work stays linear."""

    real = bc._reread
    sizes: list[int] = []

    def spy(text: bytes, **k: Any) -> Verdict:
        if k.get("command"):
            sizes.append(len(text))
        return real(text, **k)

    monkeypatch.setattr(bc, "_reread", spy)
    command = ":`a` `b`\n" * 200
    assert classify(command) == Verdict.ASK  # ``a`` and ``b`` are unknown commands
    assert sizes
    assert max(sizes) <= len(":`a` `b`")
    sizes.clear()
    assert classify("echo `echo x` `rm -rf ~`\n" + "ls\n" * 50) == Verdict.DENY
    assert max(sizes) <= len("echo `echo x` `rm -rf ~`")
    sizes.clear()
    assert classify(" && ".join(["echo `ls a` `ls b`"] * 50)) == Verdict.ALLOW
    assert max(sizes) <= len("echo `ls a` `ls b`")


def test_a_split_the_budget_cannot_pay_asks_and_is_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_judge = bc._judge
    real_reread = bc._reread
    built: list[bytes] = []

    def broke(root: Any, source: bytes, **k: Any) -> Verdict:
        own = bc._BACKTICK_BUDGET.get()
        if own is not None:
            own[0] = 0
        return real_judge(root, source, **k)

    def spy(text: bytes, **k: Any) -> Verdict:
        if k.get("command"):
            built.append(text)
        return real_reread(text, **k)

    monkeypatch.setattr(bc, "_judge", broke)
    monkeypatch.setattr(bc, "_reread", spy)
    assert classify("echo `echo x` `echo y`; echo `echo x` `echo y`") == Verdict.ASK
    assert built and all(b"echo y" not in text for text in built)  # only ``echo x`` is read


def test_a_split_asks_when_only_the_split_is_unpaid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The substitution's own reading is paid and allows; the split it cannot pay still asks."""

    real_judge = bc._judge

    def tight(root: Any, source: bytes, **k: Any) -> Verdict:
        own = bc._BACKTICK_BUDGET.get()
        if own is not None:
            own[0] = len("echo x")
        return real_judge(root, source, **k)

    monkeypatch.setattr(bc, "_judge", tight)
    assert classify("echo `echo x` `touch MARK`") == Verdict.ASK


def test_a_rejected_command_still_splits_its_whole_source() -> None:
    """In a command tree-sitter rejects, the split view keeps the statements around the pair.

    ASK on 363accf2, and DENY on 78df9ce4, whose view was the whole source;
    the ``: > MARK`` twin ran in bash, dash, both ksh93 and mksh, which run
    the first line before they reach the stray ``)``.
    """

    assert classify("echo `echo x` `rm -rf ~`\n)") == Verdict.DENY


# Rows the round-1 verify's mutants M34 and M03 changed and no test pinned.
# M34 dropped ``_reread_lines`` from ``_reread_command``: the reading
# ``echo "$(rm -rf ~)"; echo $(`` is rejected, and only its prefix finds the
# DENY. M03 floored ``_reread_lines`` at ASK: the shells end the body at
# ``A`` and run the next lines, which tree-sitter rejects, so only their
# backtick reading finds the DENY and the parsed prefix (``ls``) must keep it.
_KEPT_DENY_R1 = (
    'echo `echo "\\$(rm -rf ~)"; echo \\$(`',
    'echo `echo "\\$(rm -rf ~)"\necho \\$(`',
    "cat <<A''\nx\nA\nls\necho `echo \\$(rm -rf ~)`\nA''",
)


@pytest.mark.parametrize("command", _KEPT_DENY_R1)
def test_a_rejected_reading_keeps_the_deny_its_prefix_or_backticks_find(command: str) -> None:
    assert classify(command) == Verdict.DENY, command


def test_the_nested_joined_reading_takes_nothing_from_762s(monkeypatch: pytest.MonkeyPatch) -> None:
    """The round-1 verify's M23: the reading joined inside backticks spends only the #768 budget.

    It runs before #762's joined reading of the same command; a greedy one
    that spends every byte it may leaves #762's reading its DENY.
    """

    real = bc._reread
    first: list[bytes] = []

    def greedy(text: bytes, **k: Any) -> Verdict:
        if k.get("joined") and not first:
            first.append(text)
            budget = bc._REREAD_BUDGET.get()
            assert budget is not None
            budget[0] = 0
        return real(text, **k)

    monkeypatch.setattr(bc, "_reread", greedy)
    assert classify("echo `echo 'a" + _BN + "b'`; find . -de" + _BN + "lete") == Verdict.DENY
    assert first == [b"echo `echo 'ab'`; find . -delete"]


# --- Fix round 1: words tree-sitter reads otherwise -----------------------------------


def _rows(
    asks: tuple[str, ...], denied: tuple[str, ...], kept: tuple[tuple[str, Verdict], ...]
) -> list[Any]:
    """Each row with its verdict, under both dialects: ``asks``, their ``: > MARK`` twins, ``denied`` and ``kept``."""

    twins = tuple(c.replace(": > MARK", "rm -rf ~") for c in asks if ": > MARK" in c)
    rows = [(c, Verdict.ASK) for c in asks] + [(c, Verdict.DENY) for c in twins + denied]
    rows += list(kept)
    return [(c, v, d) for c, v in rows for d in _BASH_DIALECTS]


# #768's own, the round-1 verify's leaf grid: tree-sitter keeps these right
# sides as a ``regex`` or ``extglob_pattern`` leaf. ALLOW on 363accf2; each
# ran in the shells named (``.omc/probes/768/fix-r1/shells/``; dash, which has
# no ``[[``, expands the words of its ``[[`` command).
_LEAF_RUNS = (
    "[[ x = `:>MARK` ]]",  # all nine
    "[[ x =~ `:>MARK` ]]",  # all but mksh
    "[[ x =~ a|`:>MARK` ]]",  # bash, dash, both ksh93
    "[[ -n x && x =~ `:>MARK` ]]",  # bash, zsh, both ksh93
    "[[ x =~ `:>MARK` && 1 ]]",  # all but mksh
    "[[ x == @(`:>MARK`) ]]",  # zsh, both ksh93, Linux bash, mksh
    "[[ x =~ (`:>MARK`) ]]",  # bash, zsh, both ksh93
    "[[ x =~ ^`:>MARK`$ ]]",  # all but mksh
    "[[ x =~ x`:>MARK` ]]",  # all but mksh
    "[[ x =~ `y=\\$(: > MARK)` ]]",  # all but mksh
    "[ x =~ `:>MARK` ]",  # all nine
    "[[ x = `touch${IFS}MARK` ]]",  # all but the two zsh
    "[[ x =~ x(`: > MARK`) ]]",  # bash, zsh, both ksh93
    "[[ x =~ ^(`: > MARK`)$ ]]",  # bash, zsh, both ksh93
    "[[ x =~ <(: > MARK) ]]",  # bash
)
_LEAF_DENIED = (
    "[[ x =~ (`rm -rf ~`) ]]",
    "[[ x =~ a|(`find . -delete`) ]]",
)
_LEAF_KEPT = (
    ("[[ $x =~ ^[0-9]+$ ]]", Verdict.ALLOW),
    ("[[ $x =~ ^${re}$ ]]", Verdict.ALLOW),
    ("[[ $x == a* ]]", Verdict.ALLOW),
    ('[[ "$a" == "${b}" ]]', Verdict.ALLOW),
    ("[[ $x == ${prefix}* ]]", Verdict.ALLOW),
    ("[[ $x =~ (a|b) ]]", Verdict.ALLOW),
)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"), _rows(_LEAF_RUNS, _LEAF_DENIED, _LEAF_KEPT)
)
def test_a_pattern_leaf_is_read_as_the_shells_read_it(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# Fix round 2 (the round-2 verify's F4; the lead's call, older than #768): an
# escaped ``\\$(`` on the right of ``=~``, whose ``$(…)`` ksh93 runs. ALLOW on
# 363accf2 and 866b0e5b; each created MARK in macOS ksh93u+ and Linux ksh
# 93u+m alone (``.omc/probes/768/fix-r2/shells/``).
_KSH_RUNS = (
    "[[ x =~ \\$(: > MARK) ]]",
    "[[ x =~ a\\$(: > MARK) ]]",
    "[[ x =~ x\\$(: > MARK) ]]",
    "[[ x =~ (\\$(: > MARK)) ]]",
    "[[ x =~ ^\\$(: > MARK)$ ]]",
    "[[ -n x && x =~ \\$(: > MARK) ]]",
    "[[ x =~ \\$(: > MARK) && 1 ]]",
    "[[ x =~ \\$( : > MARK ) ]]",
    "[[ x =~ \\$(: > MARK)|a ]]",
    "[[ x =~ a|\\$(: > MARK) ]]",
)
_KSH_KEPT = (
    ("[[ $x =~ ^\\$[0-9]+$ ]]", Verdict.ALLOW),
    ("[[ $x =~ ^\\$\\{ ]]", Verdict.ALLOW),
    ("[[ $x =~ \\$\\(x\\) ]]", Verdict.ALLOW),
    ('[[ x =~ "\\$(: > MARK)" ]]', Verdict.ALLOW),
    # an escaped ``$(``, ``${`` or backtick there asks, though none of these ran
    ("[[ x =~ \\$(echo a) ]]", Verdict.ASK),
    ("[[ x =~ \\${x:-\\$(: > MARK)} ]]", Verdict.ASK),
    ("[[ x =~ a\\`: > MARK\\`b ]]", Verdict.ASK),
    # and where ksh93's reading holds no command; ALLOW on 866b0e5b
    ("[[ x = {\\${x:-a} ]]", Verdict.ASK),
)


@pytest.mark.parametrize(("command", "verdict", "dialect"), _rows(_KSH_RUNS, (), _KSH_KEPT))
def test_an_escaped_substitution_ksh_runs_in_a_pattern_asks(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


def test_a_ksh_reading_longer_than_the_limit_asks_without_being_read() -> None:
    """The finish (the round-3 verify's F5): ``[[ x =~ \\$(a)|…| ]]``, 96 KB, took 12.4 s on 1a83896b.

    tree-sitter's error recovery on the ksh93 reading of a pattern with a
    trailing ``|`` grew with the square of its size. A reading up to
    :data:`_KSH_REREAD_LIMIT` bytes is read, a longer one asks unread; the
    time bound leaves room for a slow runner.
    """

    twin = "\\$(rm -rf ~)"
    fits = "a" * (bc._KSH_REREAD_LIMIT - len(twin) + 1)
    assert classify("[[ x =~ " + fits + twin + " ]]") == Verdict.DENY
    assert classify("[[ x =~ a" + fits + twin + " ]]") == Verdict.ASK
    shape = "[[ x =~ " + "\\$(a)|" * 16000 + " ]]"
    started = time.perf_counter()
    assert classify(shape) == Verdict.ASK
    assert time.perf_counter() - started < 3.0


def test_the_misread_readings_take_nothing_from_the_other_rereads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misread reading that spends every byte it may leaves #762's comment reading and the backtick reading their DENY.

    Fix round 2 (the round-2 verify's F1): it spends the re-read budget and
    the #768 one as it finds them, and a #768 reading nested in it is paid
    from the misread budget too. In the second command the misread reading in
    the second line's backticks runs before the first line's are read.
    """

    real_misread = bc._misread_reread
    real_reread = bc._reread
    inside: list[bool] = []

    def misread(text: bytes, **k: Any) -> Verdict:
        inside.append(True)
        try:
            return real_misread(text, **k)
        finally:
            inside.pop()

    def greedy(text: bytes, **k: Any) -> Verdict:
        if inside:
            for budget in (bc._REREAD_BUDGET, bc._BACKTICK_BUDGET):
                left = budget.get()
                assert left is not None
                left[0] = 0
        return real_reread(text, **k)

    monkeypatch.setattr(bc, "_misread_reread", misread)
    monkeypatch.setattr(bc, "_reread", greedy)
    assert classify("[[ x =~ (`echo $x`) ]]; echo \\ #$(rm -rf ~)") == Verdict.DENY
    padded = _PADDED_TWIN.split("\n")[0] + "\necho `[[ x =~ (\\`echo \\$a\\`) ]]`"
    assert classify(padded) == Verdict.DENY
    # its own reading was not paid: it asks, and is not refused
    assert classify("[[ x =~ (`rm -rf ~`) ]]") == Verdict.ASK


# The round-2 verify's F1: ten lines whose backticks hold a pattern leaf with
# eight escaped substitutions, after a backtick twin that is DENY alone
# (1,354 bytes). On 866b0e5b the misread readings in those lines spent 9,350
# of the 9,372 #768 bytes, and the twin asked; DENY on 78df9ce4.
_PADDED_TWIN = "echo `echo \"${x:-'\\$(rm -rf ~)'}\"`\n" + "\n".join(
    ["echo `[[ x =~ (" + " ".join(["\\`echo \\\\$a\\`"] * 8) + ") ]]`"] * 10
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
def test_misread_readings_leave_the_backtick_budget_to_the_backticks(dialect: Dialect) -> None:
    assert len(_PADDED_TWIN) == 1354
    assert classify(_PADDED_TWIN, dialect=dialect) == Verdict.DENY


# The finish (the round-3 verify's F3): the pattern leaves of these pad lines
# pay their nested readings from the misread budget, which left the edited
# re-read of the whole source too little on 1a83896b. Each row was DENY on
# 866b0e5b and asked on 1a83896b after 3 pad lines (the ``${x:-$'…'}`` one
# after 10, before them); ALLOW on 363accf2. Classified only.
_PAD = "echo `[[ x =~ (" + " ".join(["\\`echo \\\\$a\\`"] * 8) + ") ]]`"
_MISREAD_TWINS = (
    'echo "$ $(rm -rf ~)"',
    "[ a > ~/.bashrc ]",
    'find / -de""\\lete',
    "echo == ; rm -rf ~ ; ]",
    "echo \"${x:-$'\\x24(rm -rf ~)'}\"",
)


@pytest.mark.parametrize("lines", [3, 10])
@pytest.mark.parametrize("twin", _MISREAD_TWINS)
def test_misread_readings_after_pad_lines_keep_their_deny(twin: str, lines: int) -> None:
    for command in ("\n".join([_PAD] * lines + [twin]), "\n".join([twin] + [_PAD] * lines)):
        for dialect in _BASH_DIALECTS:
            assert classify(command, dialect=dialect) == Verdict.DENY, (twin, lines, dialect)


# The lead's call, not the owner's: older holes of the same class. A ``$``,
# blanks and a ``$`` in double quotes or an expanded here-document body, which
# tree-sitter reads as ``$$``. ALLOW on 363accf2; each created MARK in all nine
# shells.
_DOLLAR_RUNS = (
    'echo "$ $(: > MARK)"',
    'echo "$\t$(: > MARK)"',
    'echo "$\n$(: > MARK)"',
    "cat <<E\n$ $(: > MARK)\nE",
    'echo "a$  $(: > MARK)"',
)
_DOLLAR_KEPT = (
    ('echo "cost: $ 5"', Verdict.ALLOW),
    ('echo "$$ $? $# $1"', Verdict.ALLOW),
    # ``$$`` to the shells too once ``\⏎`` is gone; none of the nine ran it
    ('echo "$' + _BN + '$(: > MARK)"', Verdict.ALLOW),
    # the stray ``$`` asks, though no shell runs anything here
    ('echo "$ $"', Verdict.ASK),
)


@pytest.mark.parametrize(("command", "verdict", "dialect"), _rows(_DOLLAR_RUNS, (), _DOLLAR_KEPT))
def test_a_stray_dollar_is_read_as_the_shells_read_it(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# ``==`` or ``=~`` and later a ``]`` word in a command's arguments, which
# tree-sitter reads as one ``regex``. ALLOW on 363accf2; each created MARK in
# bash, dash, both ksh93 and mksh (zsh stops at ``==``), the ``$(…)`` one in
# all nine.
_EQEQ_RUNS = (
    "echo == ; : > MARK ; ]",
    "echo =~ ; : > MARK ; ]",
    "echo x == ; : > MARK ; ]",
    "echo == && : > MARK || ]",
    "echo == $(: > MARK) ]",
    "echo == > MARK ]",
)
_EQEQ_DENIED = ("echo == > ~/.bashrc ]",)
_EQEQ_KEPT = (
    ('[ "$a" == "$b" ]', Verdict.ALLOW),
    ('[[ "$a" == "$b" ]]', Verdict.ALLOW),
    ('echo "a==b"', Verdict.ALLOW),
    ("echo a == b", Verdict.ALLOW),
    ("test x == y", Verdict.ASK),
    # a ``regex`` in a command's arguments asks, though this one hides nothing
    ("echo x == y ]", Verdict.ASK),
)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"), _rows(_EQEQ_RUNS, _EQEQ_DENIED, _EQEQ_KEPT)
)
def test_a_regex_in_arguments_is_read_as_the_shells_read_it(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# A word tree-sitter splits at a backslash after a quote. ``printf '[%s]'``
# printed ``[-delete]`` and ``[-exec]`` for these in all nine shells;
# classified only, ALLOW on 363accf2.
_SPLIT_WORD_DENIED = (
    'find / -de""\\lete',
    'find . -ex""\\ec rm -rf {} +',
    "find . -de''\\lete",
    'find . "-de"\\lete',
    'find . -de""\\l"e"te',
    'echo `find / -de""\\\\lete`',
)
_SPLIT_WORD_KEPT = (
    ('ls "a"\\b', Verdict.ALLOW),
    ('find . -name "*.py"\\ -o -name x', Verdict.ALLOW),
    ('find . -name ""\\x', Verdict.ALLOW),
    ('grep -e x""\\\'y file', Verdict.ALLOW),
    # tree-sitter splits after a bare ``$`` too; the escaped ``'`` is quoted as ``"'"``
    ('echo }x$\\\'""-de}', Verdict.ALLOW),
)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"), _rows((), _SPLIT_WORD_DENIED, _SPLIT_WORD_KEPT)
)
def test_a_word_split_after_a_quote_is_read_whole(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# ``>``, ``>>`` or ``<`` inside ``[ … ]``, which tree-sitter reads as a
# comparison and every shell as a redirect. ALLOW on 363accf2; each created
# MARK in all nine.
_BRACKET_RUNS = (
    "[ a > MARK ]",
    "[ a >> MARK ]",
    "[ a -a b > MARK ]",
)
_BRACKET_DENIED = ("[ a > ~/.bashrc ]", "[ -n x -o a >> ~/.bashrc ]")
_BRACKET_KEPT = (
    ("[ a < b ]", Verdict.ALLOW),  # a read, as ``echo a < b``
    ('[ "$a" = "$b" ]', Verdict.ALLOW),
    ("[ -f x ] && echo ok", Verdict.ALLOW),
)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"), _rows(_BRACKET_RUNS, _BRACKET_DENIED, _BRACKET_KEPT)
)
def test_a_bracket_redirect_is_read_as_a_redirect(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


def test_double_brackets_redirect_only_where_dash_may_run() -> None:
    """``[[ a > MARK ]]`` created MARK in dash alone, which has no ``[[``: a redirect under POSIX only."""

    assert classify("[[ a > ~/.bashrc ]]", dialect=Dialect.POSIX) == Verdict.DENY
    assert classify("[[ a > MARK ]]", dialect=Dialect.POSIX) == Verdict.ASK
    assert classify("[[ a > ~/.bashrc ]]", dialect=Dialect.UNKNOWN) == Verdict.ALLOW
    assert classify("[[ a < b ]]", dialect=Dialect.POSIX) == Verdict.ALLOW


# A ``$'…'`` in a ``${…}`` operand inside double quotes, which bash decodes
# and expands again. ALLOW on 363accf2; each created MARK in macOS bash 3.2.57
# and Linux bash 5.2.37 only.
_ANSI_RUNS = (
    "echo \"${x:-$'\\x24(: > MARK)'}\"",
    "echo \"${x:-$'\\044(: > MARK)'}\"",
    "echo \"${x:-$'\\\\\\$(: > MARK)'}\"",
    "echo `echo \"${x:-$'\\\\\\\\\\\\\\$(: > MARK)'}\"`",
)
_ANSI_KEPT = (
    # unquoted, no shell expands what it decoded (none of the nine ran it)
    ("echo ${x:-$'\\x24(: > MARK)'}", Verdict.ALLOW),
    # every ``$'…'`` there asks, even one that decodes to plain text
    ("echo \"${x:-$'a'}\"", Verdict.ASK),
)


@pytest.mark.parametrize(("command", "verdict", "dialect"), _rows(_ANSI_RUNS, (), _ANSI_KEPT))
def test_a_dollar_quote_bash_decodes_is_read_decoded(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# Fix round 2 (the round-2 verify's F2): a ``$'…'`` in a pattern operand of a
# ``${…}`` inside double quotes, which tree-sitter keeps as text in a
# ``regex`` leaf. ALLOW on 363accf2 and 866b0e5b; each created MARK in macOS
# bash 3.2.57 alone (``.omc/probes/768/fix-r2/shells/``).
_ANSI_PATTERN_RUNS = (
    "echo \"${PWD#$'\\x24(: > MARK)'}\"",
    "echo \"${PWD##$'\\x24(: > MARK)'}\"",
    "echo \"${PWD%$'\\x24(: > MARK)'}\"",
    "echo \"${PWD%%$'\\x24(: > MARK)'}\"",
    "echo \"${PWD/$'\\x24(: > MARK)'/b}\"",
    "echo \"${PWD//$'\\x24(: > MARK)'/b}\"",
    "echo \"${PWD/#$'\\x24(: > MARK)'/b}\"",
    "echo \"${PWD/%$'\\x24(: > MARK)'/b}\"",
    "echo \"${PATH#$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#*$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#a$'\\x24(: > MARK)'b}\"",
    "echo \"${PWD#$'\\044(: > MARK)'}\"",
    "echo \"${PWD#$'\\\\\\$(: > MARK)'}\"",
    "echo \"${PWD#$'\\x60: > MARK\\x60'}\"",
    "echo \"a${PWD%$'\\x24(: > MARK)'}b\"",
    "echo \"${PWD:+${PWD#$'\\x24(: > MARK)'}}\"",
    "echo \"${x:-${PWD#$'\\x24(: > MARK)'}}\"",
    "echo \"${PWD#@($'\\x24(: > MARK)')}\"",
    # tree-sitter ends the leaf at the ``}``, and bash reads on to the quote
    "echo \"${PWD#$'\\x24(: > MARK)}'}\"",
)
_ANSI_PATTERN_KEPT = (
    # unquoted, or in a ``"…"`` of its own, none of the nine ran it
    ("echo ${PWD#$'\\x24(: > MARK)'}", Verdict.ALLOW),
    ('echo "${PWD#"$\'\\x24(: > MARK)\'"}"', Verdict.ALLOW),
    # every ``$'…'`` there asks, even one that decodes to plain text
    ("echo \"${PWD%$'\\n'}\"", Verdict.ASK),
)
# The finish (the round-3 verify's F6): its decoded text, ``'$(rm -rf ~)``,
# is read as double-quoted text only once the escaped quote is decoded too;
# its mutants N19 and N20 made it ASK. Its ``: > MARK`` twin ran in none of the
# nine shells; classified only.
_ANSI_PATTERN_DENIED = ("echo \"${PWD/$'\\'\\x24(rm -rf ~)'/b}\"",)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"),
    _rows(_ANSI_PATTERN_RUNS, _ANSI_PATTERN_DENIED, _ANSI_PATTERN_KEPT),
)
def test_a_dollar_quote_in_a_pattern_operand_is_read_decoded(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# The finish (the round-3 verify's F1; the lead's call, older than #768):
# tree-sitter ends the ``${…}`` at a ``}`` after a backslash, where bash reads
# on, so the ``$'…'`` falls into the string's own text. ALLOW on 363accf2 and
# 1a83896b; each created MARK in macOS bash 3.2.57, the ``:-``, ``-``, ``:+``,
# ``+``, ``:?`` and ``?`` ones in Linux bash 5.2.37 too
# (``.omc/probes/768/verify-r3/out/escbrace-holes.txt``, 52 distinct forms).
_ESCAPED_BRACE_RUNS = (
    "echo \"${PWD#\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD##a\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD%%\\}\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#a\\}b$'\\x24(: > MARK)'}\"",
    "echo \"${PWD%%*\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#\\\\\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#$y\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD%\\{\\}$'\\x24(: > MARK)'}\"",
    "echo \"${PWD//a/[\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${PWD#[!\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${x:-[\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${x-[\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${x?[\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${PWD:+[\\}a]$'\\x24(: > MARK)'}\"",
    "echo \"${PWD:+[a\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${x:-[[\\}]$'\\x24(: > MARK)'}\"",
    "echo \"${x:-a[\\}]b$'\\x24(: > MARK)'}\"",
    "echo \"${PWD:+*[\\}]*$'\\x24(: > MARK)'}\"",
)
_ESCAPED_BRACE_KEPT = (
    ('echo "${PWD#\\}"', Verdict.ALLOW),
    ("echo \"$'a'\"", Verdict.ALLOW),
    # a here-document that does not expand is not read
    ("cat <<'E'\n${PWD#\\}$'\\x24(: > MARK)'}\nE", Verdict.ALLOW),
    # every ``$'`` in the text of a ``"…"`` holding a ``${…}`` asks, though
    # bash reads this one as text
    ("echo \"${x}: $'a'\"", Verdict.ASK),
    # and in an expanded here-document body, where macOS bash, zsh, dash and
    # ksh93u+ ran none of these (``.omc/probes/768/finish/shells/``)
    ("cat <<E\n${x:-[\\}]$'\\x24(: > MARK)'}\nE", Verdict.ASK),
    ("cat <<E\n${x:-[\\}]$'\\x24(rm -rf ~)'}\nE", Verdict.DENY),
)


@pytest.mark.parametrize(
    ("command", "verdict", "dialect"), _rows(_ESCAPED_BRACE_RUNS, (), _ESCAPED_BRACE_KEPT)
)
def test_a_dollar_quote_after_an_escaped_brace_is_read_decoded(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


@pytest.mark.parametrize(
    ("text", "decoded"),
    [
        (b"$'\\x24(a)'", b"$(a)"),
        (b"$'\\044(a)'", b"$(a)"),
        (b"$'\\u0024'", b"$"),
        (b"$'\\U00000024'", b"$"),
        (b"$'\\\\\\$(a)'", b"\\\\$(a)"),
        (b"$'\\ca\\n\\t\\''", b"\x01\n\t'"),
        (b"$'\\q'", b"\\q"),
        (b"$'\\U7fffffff'", "\U0010ffff".encode()),
    ],
)
def test_what_bash_decodes_in_dollar_quotes(text: bytes, decoded: bytes) -> None:
    assert bc._ansi_c_decoded(text) == decoded


# --- Real shells -------------------------------------------------------------------

_PATH_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
_SHELLS = ["bash", "zsh", "dash", "ksh", "mksh"]


def _shell_or_skip(name: str) -> str:
    if sys.platform == "win32":
        pytest.skip("POSIX shells and a file name holding $( and [")
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"no {name}")
    return path


def _run(shell: str, command: str, work: Path) -> tuple[bool, str]:
    """Run ``command`` with ``files/z[$(: > MARK)]`` planted: whether MARK appeared, and stdout."""

    (work / "files").mkdir(parents=True)
    (work / "files" / "z[$(: > MARK)]").write_bytes(b"")
    done = subprocess.run(
        [shell, "-c", command],
        cwd=work,
        env=_PATH_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return (work / "MARK").exists(), done.stdout.decode(errors="replace")


@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize(
    "command", [_RUNS[0], _RUNS[5], _MERGED_RUNS[0], 'echo "`echo \\"${x:-\'\\$(: > MARK)\'}\\"`"']
)
def test_every_shell_runs_the_unescaped_form_and_it_asks(
    command: str, shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    ran, _out = _run(path, command, tmp_path)
    assert ran, f"{shell} did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


def test_dash_runs_the_doubled_backslash_form_and_it_asks(tmp_path: Path) -> None:
    dash = _shell_or_skip("dash")
    command = _RUNS[1]
    ran, _out = _run(dash, command, tmp_path)
    assert ran, f"dash did not run {command!r}"
    assert classify(command) == Verdict.ASK, command


@pytest.mark.parametrize("shell", _SHELLS)
def test_every_shell_passes_the_joined_argument(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    _ran, out = _run(path, "printf '[%s]\\n' `printf %s '-de" + _BN + "lete'`", tmp_path)
    assert out == "[-delete]\n", (shell, out)
    assert classify("echo `find . '-de" + _BN + "lete'`") == Verdict.DENY


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_dollar_before_a_backslash_is_literal(shell: str, tmp_path: Path) -> None:
    """The premise for keeping ``\\$`` before ``\\x`` in the read text."""

    path = _shell_or_skip(shell)
    ran, out = _run(path, "printf '[%s]\\n' `printf %s \\$\\(: \\> MARK\\)`", tmp_path)
    assert not ran
    assert out == "[$(:>MARK)]\n", (shell, out)


@pytest.mark.parametrize("shell", _SHELLS)
def test_backticks_in_double_quotes_undo_an_escaped_quote(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    _ran, out = _run(path, 'printf \'[%s]\\n\' "`printf %s \\"a\\"`"', tmp_path)
    assert out == "[a]\n", (shell, out)


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_here_document_substitution_joins_except_in_dash(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    dash = shell == "dash" or Path(path).resolve().name == "dash"
    expected = "[a\\\nb]\n" if dash else "[ab]\n"
    _ran, out = _run(path, "cat <<E\n[$(printf %s 'a" + _BN + "b')]\nE", tmp_path)
    assert out == expected, (shell, out)


# Fix round 1 premises: each was ALLOW on 363accf2 and asks now.
_SHELL_RUNS_LEAF = ("[[ x = `:>MARK` ]]",)
_SHELL_RUNS_DOLLAR = ('echo "$ $(: > MARK)"', "cat <<E\n$ $(: > MARK)\nE")
_SHELL_RUNS_EQEQ = ("echo == $(: > MARK) ]",)
_SHELL_RUNS_BRACKET = ("[ a > MARK ]", "[ -n x -o a >> MARK ]")


@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize(
    "command", _SHELL_RUNS_LEAF + _SHELL_RUNS_DOLLAR + _SHELL_RUNS_EQEQ + _SHELL_RUNS_BRACKET
)
def test_every_shell_runs_what_tree_sitter_misreads_and_it_asks(
    command: str, shell: str, tmp_path: Path
) -> None:
    path = _shell_or_skip(shell)
    ran, _out = _run(path, command, tmp_path)
    assert ran, f"{shell} did not run {command!r}"
    assert classify(command, dialect=Dialect.POSIX) == Verdict.ASK, command


@pytest.mark.parametrize("shell", _SHELLS)
def test_every_shell_runs_the_lines_after_the_body_it_ends(shell: str, tmp_path: Path) -> None:
    """The premise of the M03 row: the shells end the body at ``A`` and run the next lines."""

    path = _shell_or_skip(shell)
    command = "cat <<A''\nx\nA\nls\necho `echo \\$(: > MARK)`\nA''"
    ran, _out = _run(path, command, tmp_path)
    assert ran, shell
    assert classify(command.replace(": > MARK", "rm -rf ~")) == Verdict.DENY


@pytest.mark.parametrize("shell", ["bash", "dash", "ksh", "mksh"])
def test_the_statements_after_a_lone_eqeq_run(shell: str, tmp_path: Path) -> None:
    """zsh expands ``==`` as a command name and stops; the others run the next statements."""

    path = _shell_or_skip(shell)
    ran, _out = _run(path, "echo == ; : > MARK ; ]", tmp_path)
    assert ran, shell
    assert classify("echo == ; : > MARK ; ]") == Verdict.ASK


def test_dash_redirects_inside_double_brackets(tmp_path: Path) -> None:
    dash = _shell_or_skip("dash")
    ran, _out = _run(dash, "[[ a > MARK ]]", tmp_path)
    assert ran
    assert classify("[[ a > MARK ]]", dialect=Dialect.POSIX) == Verdict.ASK


def test_bash_expands_a_decoded_dollar_quote_again(tmp_path: Path) -> None:
    bash = _shell_or_skip("bash")
    ran, _out = _run(bash, _ANSI_RUNS[0], tmp_path)
    assert ran
    assert classify(_ANSI_RUNS[0]) == Verdict.ASK


def test_bash_3_expands_a_decoded_pattern_operand_again(tmp_path: Path) -> None:
    """macOS's ``/bin/bash`` 3.2 runs it; Linux bash 5.2.37 did not (the round-2 verify)."""

    bash = _shell_or_skip("bash")
    version = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, check=False, timeout=60
    )
    if version.stdout.strip() != b"3":
        pytest.skip("bash 3.2 decodes and expands a pattern operand's $'...' again")
    ran, _out = _run(bash, _ANSI_PATTERN_RUNS[0], tmp_path)
    assert ran
    assert classify(_ANSI_PATTERN_RUNS[0]) == Verdict.ASK


def test_ksh93_runs_an_escaped_substitution_in_a_pattern(tmp_path: Path) -> None:
    ksh = _shell_or_skip("ksh")
    version = subprocess.run(
        [ksh, "-c", "echo ${.sh.version}"], capture_output=True, check=False, timeout=60
    )
    if b"93" not in version.stdout:
        pytest.skip("ksh93 runs the $(...) of an escaped \\$( on the right of =~")
    ran, _out = _run(ksh, _KSH_RUNS[0], tmp_path)
    assert ran
    assert classify(_KSH_RUNS[0]) == Verdict.ASK


@pytest.mark.parametrize("shell", _SHELLS)
def test_every_shell_joins_a_word_split_after_a_quote(shell: str, tmp_path: Path) -> None:
    path = _shell_or_skip(shell)
    command = 'printf \'[%s]\' -de""\\lete -ex""\\ec "-de"\\lete -de\'\'\\lete'
    _ran, out = _run(path, command, tmp_path)
    assert out == "[-delete][-exec][-delete][-delete]", (shell, out)
    assert classify('find / -de""\\lete') == Verdict.DENY


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


# The first, second and fourth ran with no dialog and created MARK in a pty
# TUI under ``auto`` on 363accf2 (``SHELL=/bin/bash``, ``/bin/ksh`` and
# ``/bin/bash``; ``.omc/probes/768/impl/live/``), and open the dialog now.
_GATE_ASK = (
    _RUNS[0],
    _RUNS[1],
    _RUNS[5],
    _MERGED_RUNS[0],
    # fix round 1: each ran with no dialog and created MARK in a pty TUI under
    # ``auto`` with ``SHELL=/bin/bash`` on 363accf2, and opens the dialog now
    # (``.omc/probes/768/fix-r1/live/``)
    "[[ x = `:>MARK` ]]",
    'echo "$ $(: > MARK)"',
    "echo == ; : > MARK ; ]",
    "[ a > MARK ]",
)
_GATE_READS = (
    "echo `date`",
    "echo `echo \\$HOME`",
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
