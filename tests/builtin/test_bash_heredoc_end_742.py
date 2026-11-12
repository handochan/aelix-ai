"""#742 — a here-document body ends where bash, zsh, dash and ksh end it, not where tree-sitter does.

On b160b8f7 ``cat <<A''`` + ``x`` + ``A`` + ``find victim -delete`` + ``A''`` was
ALLOW. tree-sitter-bash 0.25.1 compares a body line with the RAW delimiter
unless the delimiter starts with a quote or a backslash, so it ended the body
at ``A''`` and the ``find`` sat inside a quoted body nobody walks. bash, zsh,
dash and ksh remove the quotes first, end the body at ``A`` and delete
``victim``.

The same measurement found tree-sitter ending a body too EARLY, at a line that
only holds the delimiter among blanks or before ``;``, ``#…`` or ``\\r``
(``EOF ``, `` EOF``, ``EOF;``), for plain ``<<EOF`` and ``<<'EOF'`` too. The
shells keep reading there, so a ``cat <<'Q'`` after tree-sitter's end swallows
the line the shells run next. And with a delimiter that has no quoted part the
shells join a body line ending in an odd number of backslashes to the next,
which tree-sitter does not.

Every row's shell column was measured with ``<shell> -c`` in an empty scratch
directory holding ``victim``: macOS bash 3.2.57, zsh 5.9, dash and ksh93u+
2012-08-01, and Linux (docker ``python:3.12-slim``) bash 5.2.37, zsh 5.9, dash
and ksh 93u+m/1.0.10, with the same result on both (``.omc/probes/742/impl/``).

Verdicts are compared with ``==``, not ``is``: ``test_bash_classifier.py``
reloads the classifier module last, which rebinds ``Verdict``.
"""

from __future__ import annotations

import time
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
_FIND = "find victim -delete"

# (command, verdict). Every shell measured deleted ``victim`` unless the
# comment names the ones that did. ALLOW on b160b8f7 unless noted.
_SHELLS_END_ELSEWHERE = (
    # The issue's forms: the shells end at the quote-removed delimiter.
    (f"cat <<A''\nx\nA\n{_FIND}\nA''", Verdict.DENY),
    (f'cat <<A""\nx\nA\n{_FIND}\nA""', Verdict.DENY),
    (f"cat <<A'B'\nx\nAB\n{_FIND}\nA'B'", Verdict.DENY),
    (f'cat <<A"B"\nx\nAB\n{_FIND}\nA"B"', Verdict.DENY),
    (f"cat <<A''B\nx\nAB\n{_FIND}\nA''B", Verdict.DENY),
    (f"cat <<A'B'C\nx\nABC\n{_FIND}\nA'B'C", Verdict.DENY),
    (f"cat <<-A''\nx\nA\n{_FIND}\nA''", Verdict.DENY),
    (f"cat <<-A''\n\tx\n\tA\n{_FIND}\nA''", Verdict.DENY),  # ``<<-`` strips the tab
    (f"cat <<EOF''\nx\nEOF\n{_FIND}\nEOF''", Verdict.DENY),
    ("cat <<EOF''\nx\nEOF\nrm -rf ~\nEOF''", Verdict.DENY),  # removed the scratch HOME
    (f"cat <<A''\nx\nA\n{_FIND}\nA''\n", Verdict.DENY),
    # The rest of the first line, or a statement before, changes nothing.
    (f"cat <<A'' | cat\nx\nA\n{_FIND}\nA''", Verdict.DENY),
    (f"cat <<A'' >/dev/null\nx\nA\n{_FIND}\nA''", Verdict.DENY),
    (f"ls\ncat <<A''\nx\nA\n{_FIND}\nA''", Verdict.DENY),
    (f"cat <<A''\nx\nA\n{_FIND}\nA'' && ls", Verdict.DENY),
    # Nested: ASK on b160b8f7 for ``$(…)``, ALLOW for the subshell. Only the
    # lines up to tree-sitter's end parse on their own inside ``$(…)``.
    (f"echo \"$(cat <<A''\nx\nA\n{_FIND}\nA''\n)\"", Verdict.DENY),
    (f"(cat <<A''\nx\nA\n{_FIND}\nA''\n)", Verdict.DENY),
    # CRLF throughout: the delimiter word is ``A''\r``, so the shells end at ``A\r``.
    (f"cat <<A''\r\nx\r\nA\r\n{_FIND} #\r\nA''\r\n", Verdict.DENY),
    # A quoted delimiter joins no lines: ``x\`` and then ``A`` ends the body.
    (f"cat <<A''\nx\\\nA\n{_FIND}\nA''", Verdict.DENY),
    # The shells end LATER than tree-sitter, whose own end line came first; the
    # ``cat <<'Q'`` after it hides the ``find`` from tree-sitter.
    (f"cat <<A''\nA''\ncat <<'Q'\nA\n{_FIND}\nQ", Verdict.DENY),
    # tree-sitter ends early at a loose line, plain delimiters included.
    *(
        (f"cat {op}{start}\n{line}\ncat <<'Q'\nEOF\n{_FIND}\nQ", Verdict.DENY)
        for op, start in (("<<", "EOF"), ("<<", "'EOF'"), ("<<-", "EOF"))
        for line in ("EOF ", " EOF", "EOF;", "EOF #c", "EOF\r")
    ),
    (f"cat <<EOF\n\tEOF\ncat <<'Q'\nEOF\n{_FIND}\nQ", Verdict.DENY),
    (f"cat <<'EOF'\n\tEOF\ncat <<'Q'\nEOF\n{_FIND}\nQ", Verdict.DENY),
    # An unquoted delimiter joins ``x\`` to ``EOF``, so the shells end at the
    # second ``EOF``.
    (f"cat <<EOF\nx\\\nEOF\ncat <<'Q'\nEOF\n{_FIND}\nQ", Verdict.DENY),
    # The shells never end this body and expand all of it; single quotes are
    # data in a body, so the ``$(…)`` runs.
    (f"cat <<EOF\nEOF \necho '$({_FIND})'", Verdict.DENY),
    # ``$'…'`` / ``$"…"``: ASK on b160b8f7. Shells that deleted, by line: ``A``
    # — bash, zsh, ksh; ``$A`` — dash (``$'A'``), zsh and dash (``$"A"``).
    (f"cat <<A$''\nx\nA\n{_FIND}\nA$''", Verdict.DENY),
    (f"cat <<$'A'\nx\nA\n{_FIND}\n$'A'", Verdict.DENY),
    (f"cat <<$'A'\nx\n$A\n{_FIND}\n$'A'", Verdict.DENY),
    (f'cat <<$"A"\nx\nA\n{_FIND}\n$"A"', Verdict.DENY),
    (f'cat <<$"A"\nx\n$A\n{_FIND}\n$"A"', Verdict.DENY),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(("command", "verdict"), _SHELLS_END_ELSEWHERE)
def test_the_lines_after_the_shells_end_are_read(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# The shells and tree-sitter end these bodies on the same line: no shell ran
# the ``find`` or wrote a file, and the verdicts are b160b8f7's.
_SAME_END = (
    ("cat <<EOF\nx\nEOF", Verdict.ALLOW),
    ("cat <<'EOF'\nx\nEOF", Verdict.ALLOW),
    ('cat <<"EOF"\nx\nEOF', Verdict.ALLOW),
    ("cat <<-EOF\n\tx\n\tEOF", Verdict.ALLOW),
    ("cat <<\\EOF\nx\nEOF", Verdict.ALLOW),
    ("cat <<EOF\nx\nEOF\n", Verdict.ALLOW),
    (f"cat <<'EOF'\n{_FIND}\nEOF", Verdict.ALLOW),  # body text, printed
    ("cat <<EOF\r\nx\r\nEOF\r\n", Verdict.ALLOW),
    ("cat <<'EOF'\r\nx\r\nEOF\r\n", Verdict.ALLOW),
    ("cat <<EOF\nfoo \\\nbar\nEOF", Verdict.ALLOW),  # joined, then ``EOF``
    ("cat <<EOF\nx\\\\\nEOF", Verdict.ALLOW),  # an even run joins nothing
    ("cat <<'EOF'\nx\\\nEOF", Verdict.ALLOW),
    # A backslash quotes the delimiter too: no line is joined (every shell
    # printed ``x\`` and ended at the first ``EOF``).
    ("cat <<\\EOF\nx\\\nEOF", Verdict.ALLOW),
    ("cat <<E\\OF\nx\\\nEOF", Verdict.ALLOW),
    # ``<<-`` strips the tab: the shells end at ``\tEOF`` as tree-sitter does,
    # and the ``find`` is in ``Q``'s body for both.
    (f"cat <<-EOF\n\tEOF\ncat <<'Q'\nEOF\n{_FIND}\nQ", Verdict.ALLOW),
    # tree-sitter reads ``find`` as a command; the shells joined ``x\`` and
    # ``EOF`` and printed it.
    (f"cat <<EOF\nx\\\nEOF\n{_FIND}\nEOF", Verdict.DENY),
    (f"cat <<EOF\nx\\\\\nEOF\n{_FIND}\nEOF", Verdict.DENY),  # all four deleted
    # A leading quote or backslash: tree-sitter removes it too.
    (f"cat <<'A'\nx\nA\n{_FIND}\n'A'", Verdict.DENY),
    (f"cat <<A\\B\nx\nAB\n{_FIND}\nA\\B", Verdict.DENY),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize(("command", "verdict"), _SAME_END)
def test_a_body_both_end_on_the_same_line_keeps_its_verdict(
    command: str, verdict: Verdict, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == verdict, command


# The shells never end these bodies (they read to the end of the input) while
# tree-sitter ends them at a loose line, so the verdict asks (ALLOW on
# b160b8f7). Nothing ran in any shell measured; the body is printed.
_SHELLS_NEVER_END = (
    "cat <<EOF\nx\nEOF ",
    "cat <<EOF\nx\nEOF\r",
    "cat <<'EOF'\nEOF \necho hi",
    "cat <<A''\r\nx\r\nA\nfind victim -delete #\r\nA''\r\n",  # CRLF start, LF end
)


@pytest.mark.parametrize("command", _SHELLS_NEVER_END)
def test_a_body_the_shells_never_end_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


# --- The pieces ---


@pytest.mark.parametrize(
    ("word", "readings"),
    [
        (b"EOF", {b"EOF"}),
        (b"A''", {b"A"}),
        (b"A'B'C", {b"ABC"}),
        (b'A"B"', {b"AB"}),
        (b"A\\B", {b"AB"}),
        (b"A\\\\", {b"A\\"}),
        (b'"A\\"B"', {b'A"B'}),
        (b'X"A\\x"', {b"XA\\x"}),  # a backslash before another byte stays
        (b'"A\\$"', {b"A$"}),
        # ``\`` + newline inside double quotes is removed: all eight shells
        # ended ``cat <<"A\`` + newline + ``B"`` at ``AB`` (fix round 1).
        (b'"A\\\nB"', {b"AB"}),
        (b"A''\r", {b"A\r"}),
        (b"A$x", {b"A$x"}),  # no shell expands a delimiter
        (b"$'A'", {b"$A", b"A"}),  # dash; bash, zsh and ksh93
        (b'$"A"', {b"$A", b"A", b'$"A"'}),  # dash and zsh; bash; ksh93
        (b"A$''", {b"A$", b"A"}),
    ],
)
def test_each_shell_familys_delimiter(word: bytes, readings: set[bytes]) -> None:
    assert bc._delimiter_readings(word) == readings


@pytest.mark.parametrize("word", [b"$'A\\x41'", "É''".encode(), b"\xc3\x84", b"A'B", b'A"B'])
def test_a_delimiter_some_shell_reads_unknowably_has_no_readings(word: bytes) -> None:
    """An escape to decode, a byte above ASCII (macOS ksh93 ended ``<<É''`` at no line), an open quote."""

    assert bc._delimiter_readings(word) is None


def test_the_shell_word_ends_at_a_blank_or_metacharacter_outside_quotes() -> None:
    source = b"cat <<A'; x'B;ls"
    assert bc._shell_word_end(source, 6) == source.index(b";ls")
    assert bc._shell_word_end(b"A''\r\nx", 0) == 4  # ``\r`` is part of the word
    assert bc._shell_word_end(b'A"\\"";', 0) == 5
    assert bc._shell_word_end(b"A\\ B C", 0) == 4
    assert bc._shell_word_end(b"A'open", 0) is None


def test_the_shells_end_line() -> None:
    lines = b"x\n\tA\nA \nA\\\nA\nA\n"  # lines start at 0, 2, 5, 8, 11, 13
    end = bc._shell_end_line
    assert end(lines, 0, frozenset({b"A"}), strip_tabs=False, joins=False) == (11, 12)
    assert end(lines, 0, frozenset({b"A"}), strip_tabs=True, joins=False) == (2, 4)
    # ``A\`` joins ``A`` into ``AA``; the next ``A`` ends it.
    assert end(lines, 8, frozenset({b"A"}), strip_tabs=False, joins=True) == (13, 14)
    assert end(lines, 8, frozenset({b"A"}), strip_tabs=False, joins=False) == (11, 12)
    assert end(b"x\\\\\nA", 0, frozenset({b"A"}), strip_tabs=False, joins=True) == (4, 5)
    assert end(b"x\nA", 0, frozenset({b"A"}), strip_tabs=False, joins=True) == (2, 3)  # no newline
    assert end(lines, 0, frozenset({b"B"}), strip_tabs=True, joins=True) is None
    assert end(b"A\\", 0, frozenset({b"A"}), strip_tabs=False, joins=True) is None


def test_a_here_document_without_a_start_asks() -> None:
    redirect = _Node("heredoc_redirect", (_Node("<<", named=False),))
    assert bc._heredoc_end_verdict(redirect, dialect=Dialect.UNKNOWN) == Verdict.ASK


class _Node:
    def __init__(
        self,
        type_: str,
        children: tuple[Any, ...] = (),
        *,
        named: bool = True,
        text: bytes = b"",
        span: tuple[int, int] = (0, 0),
    ) -> None:
        self.type = type_
        self.children = children
        self.is_named = named
        self.text = text
        self.start_byte, self.end_byte = span
        self.parent: Any = None


@pytest.mark.parametrize(
    "command",
    [
        "cat <<A'open\nx\nA",  # a quote left open
        "cat <<$'A\\x41'\nx\nAA\nls\n$'A\\x41'",  # an escape no reading decodes
    ],
)
def test_a_delimiter_whose_end_cannot_be_known_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


def test_an_unknown_reading_asks_on_its_own() -> None:
    """Not only through the start check and the parse error.

    Today every such word also fails to parse or has a ``$`` the start check
    asks on, so the branch is reached here through hand-built nodes.
    """

    source = b"cat <<$'A\\x41'\nx\nAA"
    start = _Node("heredoc_start", span=(6, 14))
    redirect = _Node("heredoc_redirect", (_Node("<<", named=False), start), span=(4, len(source)))
    redirect.parent = _Node("program", (redirect,), text=source)
    assert bc._heredoc_end_verdict(redirect, dialect=Dialect.UNKNOWN) == Verdict.ASK


# --- Cost: a run of mismatched here-documents stays linear ---


class _CountingParser:
    """Counts the bytes parsed, and stops the walk once they pass ``limit``."""

    def __init__(self, parser: Any, limit: int) -> None:
        self.parser = parser
        self.limit = limit
        self.parsed = 0

    def parse(self, source: bytes) -> Any:
        self.parsed += len(source)
        if self.parsed > self.limit:
            raise RuntimeError("parsed past the limit")  # classify() turns it into ASK
        return self.parser.parse(source)


@pytest.mark.parametrize("count", [50, 400])
def test_the_rereads_of_mismatched_here_documents_stay_linear(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each re-read is charged to a budget of a few passes over the source.

    Each re-read walks the here-documents in it again: without the budget 16
    of them parsed 6,632 times the input (1.8 s) and it grows exponentially;
    with it, 400 parse 5.5 times the input (measured, macOS, Python 3.12).
    """

    command = "cat <<A''\nx\nA\nls\nA''\n" * count
    budget = bc._REREAD_PASSES * len(command) + bc._REREAD_SLACK
    limit = 2 * (len(command) + budget)
    counter = _CountingParser(bc._PARSER, limit)
    monkeypatch.setattr(bc, "_PARSER", counter)
    assert classify(command) == Verdict.ASK
    assert counter.parsed <= limit


def test_a_reread_the_budget_cannot_pay_leaves_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    command = f"cat <<A''\nx\nA\n{_FIND}\nA''"
    assert classify(command) == Verdict.DENY
    monkeypatch.setattr(bc, "_REREAD_PASSES", 0)
    monkeypatch.setattr(bc, "_REREAD_SLACK", 0)
    assert classify(command) == Verdict.ASK


def test_the_first_of_many_mismatched_here_documents_is_still_read() -> None:
    one = "cat <<A''\nx\nA\nls\nA''\n"
    command = "cat <<A''\nx\nA\nrm -rf ~\nA''\n" + one * 400
    assert classify(command) == Verdict.DENY


@pytest.mark.parametrize(
    ("before", "pad", "verdict"),
    [(22, 0, Verdict.DENY), (23, 0, Verdict.ASK), (3, 4500, Verdict.DENY), (4, 4500, Verdict.ASK)],
)
def test_which_hidden_line_is_refused_depends_on_sizes(
    before: int, pad: int, verdict: Verdict
) -> None:
    """The ADR's and CHANGELOG's numbers: never ALLOW, and the threshold moves with padding."""

    one = "cat <<A''\nx\nA\nls\nA''\n"
    hidden = "cat <<A''\nx\nA\n" + ("#" + "p" * pad + "\n" if pad else "") + "rm -rf ~\nA''\n"
    assert classify(one * before + hidden) == verdict


def test_four_passes_over_the_command_pay_for_a_long_hidden_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is four passes over the command, not only the 4 KiB slack.

    The hidden line's re-read is 5.4 KB, more than the slack alone pays for.
    """

    command = "cat <<A''\nx\nA\n" + "echo pad\n" * 600 + f"{_FIND}\nA''\n"
    assert len(command) > bc._REREAD_SLACK
    assert classify(command) == Verdict.DENY
    monkeypatch.setattr(bc, "_REREAD_PASSES", 0)
    assert classify(command) == Verdict.ASK


# --- Fix round 1: a here-document inside a command or process substitution ---
#
# Inside backticks all eight shells end the substitution at its first unescaped
# backtick, in a here-document body too, and remove ``\`` + newline before they
# read the here-document, quoted delimiter or not; macOS bash 3.2.57 does both
# with ``)`` inside ``$( )``, ``<( )`` and ``>( )`` (the round-1 verify's R1-R4,
# measured again with ``<shell> -c`` on both systems, ``.omc/probes/742/fix-r1/``).
# tree-sitter knows neither, so any here-document in a substitution asks, and
# what the shells read instead is re-read.

# ALLOW on 61eda564 unless noted; each deleted ``victim`` in the shells named.
_IN_SUBSTITUTION_RUNS = (
    # R1, all eight: ``EO\`` + newline + ``F`` is joined into ``EOF``.
    f"echo `cat <<'EOF'\nx\nEO\\\nF\n{_FIND}\nEOF\n`",
    f"echo \"`cat <<'EOF'\nx\nEO\\\nF\n{_FIND}\nEOF\n`\"",  # ASK on 61eda564
    # R2, all eight: the backtick in the body ends the substitution.
    f"echo `cat <<'EOF'\n`\n{_FIND}\necho `echo\nEOF\n`",
    f"echo $(echo `cat <<'EOF'\n`\n{_FIND}\necho `echo\nEOF\n`)",
    # bash 3.2 and 5.2: the outer backticks end inside the inner ``$( )``'s body.
    f"echo `echo $(cat <<'EOF'\n`\n{_FIND}\necho `echo\nEOF\n)`",
    # R3, bash, dash and ksh on both systems: ``EOF``` ends it and ``Q`` never does.
    f"echo `cat <<'EOF'\nx\nEOF\ncat <<'Q'\nEOF`\n{_FIND}\nQ\n`",
    # R4 and its kin, macOS bash 3.2.57 only.
    f"echo $(cat <<'EOF'\n)\n{_FIND}\necho $(echo\nEOF\n)",
    f"x=$(cat <<'EOF'\n)\n{_FIND}\necho $(echo\nEOF\n)",  # ASK on 61eda564
    f"cat <(cat <<'EOF'\n)\n{_FIND}\necho <(echo\nEOF\n)",
    f"echo >(cat <<'EOF'\n)\n{_FIND}\necho >(echo\nEOF\n)",
    f"echo $(cat <<'EOF'\nx\nEO\\\nF\n{_FIND}\nEOF\n)",
    f"echo \"$(cat <<'EOF'\nx\nEO\\\nF\n{_FIND}\nEOF\n)\"",  # ASK on 61eda564
    # Fix round 2, macOS bash 3.2.57 only: its ``)`` is looked for from the
    # first body line, not in the start word ``'E)'`` (DENY on ce3cc695 too;
    # scanning from the substitution's start makes them ASK).
    f"echo $(cat <<'E)'\nx\n)\n{_FIND}\nE)\n)",
    f"echo $(cat <<'EOF)'\nx\n$EOF)\n{_FIND}\nEOF)\n)",
    f"echo $(cat <<'E)'\n)\n{_FIND}\necho $(echo\nE)\n)",
    f'echo $(cat <<"E)"\n)\n{_FIND}\necho $(echo\nE)\n)',
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _IN_SUBSTITUTION_RUNS)
def test_what_the_shells_read_inside_a_substitution_is_refused(
    command: str, dialect: Dialect
) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


# No shell ran the ``find`` or wrote a file; each asks, the cost of the rule.
# ALLOW on 61eda564 for the second, third and fourth, ASK for the rest.
_IN_SUBSTITUTION_ASKS = (
    "echo `cat <<'EOF'\nhi\nEOF`",
    "echo $(cat <<EOF\nhi\nEOF\n)",
    "cat <(cat <<EOF\nhi\nEOF\n)",
    f"echo $(cat <<'EOF'\n1) {_FIND}\nEOF\n)",  # bash 3.2 passes ``find`` to ``echo``
    # Inside double quotes what follows the early end stays in the string.
    f"echo \"$(cat <<'EOF'\n)\n{_FIND}\necho $(echo\nEOF\n)\"",
    f"echo \"`cat <<'EOF'\n`\n{_FIND}\necho `echo\nEOF\n`\"",
    f"x=\"$(cat <<'EOF'\n)\n{_FIND}\necho $(echo\nEOF\n)\"",
    "echo \"$(cat <<'EOF'\nmsg\nEOF\n)\"",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _IN_SUBSTITUTION_ASKS)
def test_a_here_document_inside_a_substitution_asks(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.ASK, command


# ``( … )``, ``{ … }`` and a function body need no such rule: the shells parse
# them as they parse the rest. Only ksh93 (both builds) ends ``( cat <<EOF`` +
# ``hi`` + ``EOF)`` at ``EOF)``, as tree-sitter does; no shell ended a body at
# ``EOF}``. Verdicts as on 61eda564.
_GROUPED = (
    (f"( cat <<EOF\nhi\nEOF)\n{_FIND}\nEOF\n", Verdict.DENY),  # ksh93 deleted
    (f"( cat <<'EOF'\nhi\nEOF)\n{_FIND}\nEOF\n)", Verdict.ASK),  # ksh93 deleted
    ("( cat <<EOF\nhi\nEOF)", Verdict.ASK),
    ("{ cat <<EOF\nhi\nEOF\n}", Verdict.ALLOW),
    (f"{{ cat <<EOF\nhi\nEOF}}\n{_FIND}\nEOF\n}}", Verdict.ASK),  # none deleted
)


@pytest.mark.parametrize(("command", "verdict"), _GROUPED)
def test_a_grouped_here_document_keeps_its_verdict(command: str, verdict: Verdict) -> None:
    assert classify(command) == verdict, command


def test_a_substitution_without_a_here_document_is_not_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []

    def spy(text: bytes, **_k: Any) -> Verdict:
        calls.append(text)
        return Verdict.ASK

    monkeypatch.setattr(bc, "_reread", spy)
    assert classify("echo `ls` $(pwd) <(true)") == Verdict.ALLOW
    assert calls == []


def _substitution_rereads(command: str, monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    seen: list[bytes] = []

    def spy(text: bytes, **_k: Any) -> Verdict:
        seen.append(text)
        return Verdict.ASK

    monkeypatch.setattr(bc, "_reread", spy)
    source = command.encode()
    root = bc._PARSER.parse(source).root_node
    for sub, heredocs in bc._substituted_heredocs(root):
        bc._substitution_rereads(sub, heredocs, source, Dialect.UNKNOWN)
    return seen


@pytest.mark.parametrize(
    ("command", "texts"),
    [
        # Backticks: ``\$``, ``\```, ``\\`` and ``\`` + newline unescaped.
        ("echo `cat <<'E'\n\\$x \\` \\\\ \\\nE\n`", [b"cat <<'E'\n$x ` \\ E\n"]),
        # The first unescaped backtick ends it: what follows stays on ``echo``'s line.
        ("echo `cat <<'E'\n`\nls\nE\n`", [b"cat <<'E'\n", b"echo x\nls\nE\n`"]),
        # bash 3.2's ``)``: not one a ``(`` in the body opened, nor an escaped one.
        ("echo $(cat <<'E'\n(x)\n)\nls\nE\n)", [b"cat <<'E'\n(x)\n", b"echo x\nls\nE\n)"]),
        ("echo $(cat <<'E'\n\\)\nE\n)", []),
        ("echo $(cat <<'E'\nx\\\nE\n)", [b"cat <<'E'\nxE\n"]),
    ],
)
def test_the_substitution_rereads(
    command: str, texts: list[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _substitution_rereads(command, monkeypatch) == texts


# --- Fix round 1: running out of stack never lowers a verdict ---


def _deepest_denied(prefix: str) -> int:
    """The deepest ``echo $($(…find…))`` nest :func:`classify` still refuses after ``prefix``."""

    def nest(depth: int) -> str:
        return prefix + "echo " + "$(" * depth + _FIND + ")" * depth

    low, high = 1, 4000
    while low < high:
        mid = (low + high + 1) // 2
        if classify(nest(mid)) == Verdict.DENY:
            low = mid
        else:
            high = mid - 1
    return low


def test_a_mismatched_here_document_before_a_deep_nest_keeps_its_deny() -> None:
    """The round-1 verify's form: a re-read on top of the walk ran out of stack.

    On 61eda564 ``cat <<A''`` + ``x`` + ``A`` + ``A''`` before the nest lost the
    DENY three levels earlier than the nest alone (327 against 330 on macOS,
    Python 3.12; the depth depends on the stack and the Python version).
    """

    assert _deepest_denied("cat <<A''\nx\nA\nA''\n") == _deepest_denied("")


def test_a_visible_deny_survives_a_run_of_mismatched_rereads() -> None:
    """Codex's 103,172-byte input: DENY on 80ff2cc2, ASK on 61eda564 (all four shells delete)."""

    command = f"{_FIND}\n#" + "x" * 100000 + "\n" + "cat <<A''\nx\nA\nls\nA''\n" * 150
    assert classify(command) == Verdict.DENY


def test_a_reread_that_runs_out_of_stack_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    def deep(*_a: Any, **_k: Any) -> Verdict:
        raise RecursionError

    monkeypatch.setattr(bc, "_reread_lines", deep)
    assert bc._reread(b"ls", dialect=Dialect.UNKNOWN) == Verdict.ASK
    monkeypatch.setattr(bc, "_heredoc_end_reads", deep)
    assert classify(f"{_FIND}\ncat <<A''\nx\nA\nA''") == Verdict.DENY
    monkeypatch.setattr(bc, "_substituted_heredocs", deep)
    assert classify(f"{_FIND}\necho $(cat <<A\nx\nA\n)") == Verdict.DENY


def test_rereads_nested_past_the_limit_are_not_made() -> None:
    token = bc._REREAD_DEPTH.set(bc._REREAD_DEPTH_LIMIT)
    try:
        assert bc._reread(_FIND.encode(), dialect=Dialect.UNKNOWN) == Verdict.ASK
    finally:
        bc._REREAD_DEPTH.reset(token)
    assert bc._reread(_FIND.encode(), dialect=Dialect.UNKNOWN) == Verdict.DENY


# --- Fix round 1: a re-read that stops parsing keeps what came before ---


@pytest.mark.parametrize(
    "command",
    [
        # Codex: all eight shells deleted ``victim`` and read ``Z``'s body to the end.
        f"cat <<A''\nx\nA\n{_FIND}\ncat <<Z\nA''\n",
        f"cat <<A''\nx\nA\n{_FIND}; cat <<Z\nA''\n",
    ],
)
def test_a_deny_before_a_later_parse_error_is_kept(command: str) -> None:
    assert classify(command) == Verdict.DENY, command


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        (f"{_FIND}\ncat <<Z\nA''\n", Verdict.DENY),
        (f"\n{_FIND}\nQ\n`", Verdict.DENY),  # one ERROR around it all, the stray ``\``` last
        (f"{_FIND}; cat <<Z", Verdict.DENY),
        # Statement by statement: a statement before the error on its line counts,
        # though bash runs no part of a line it cannot parse.
        (f"ls\n{_FIND} (", Verdict.DENY),
        ("ls (\nfind", Verdict.ASK),
        ("(", Verdict.ASK),
        ("ls\n)", Verdict.ASK),
        # tree-sitter's error starts at byte 0 here, so lines are dropped from the
        # end until it parses; all eight shells deleted ``victim`` (``if#c`` is a
        # command word to them).
        (f"{_FIND}\nif" + "#c\n" * 30, Verdict.DENY),
    ],
)
def test_the_parseable_prefix_of_a_reread(text: str, verdict: Verdict) -> None:
    assert bc._reread_lines(text.encode(), dialect=Dialect.UNKNOWN) == verdict, text


def test_the_prefix_is_cut_at_the_first_error_not_line_by_line() -> None:
    """One ERROR around it all: one cut to the stray backtick, not one parse per line after it."""

    text = f"\n{_FIND}\nQ\n`" + "#c\n" * 100
    root = bc._PARSER.parse(text.encode()).root_node
    assert bc._error_byte(root) == text.index("`")
    token = bc._REREAD_BUDGET.set([400])
    try:
        assert bc._reread_lines(text.encode(), dialect=Dialect.UNKNOWN) == Verdict.DENY
    finally:
        bc._REREAD_BUDGET.reset(token)


# --- Fix round 1: the search for the end line is paid from the budget too ---


def test_a_scan_the_budget_cannot_pay_stops() -> None:
    source = b"x\n" * 500
    token = bc._REREAD_BUDGET.set([10])
    try:
        got = bc._shell_end_line(source, 0, frozenset({b"A"}), strip_tabs=False, joins=False)
        assert got == bc._UNPAID
        assert bc._shell_word_end(b"A" * 20 + b" x", 0) is None
    finally:
        bc._REREAD_BUDGET.reset(token)
    assert bc._shell_end_line(source, 0, frozenset({b"A"}), strip_tabs=False, joins=False) is None
    assert bc._shell_word_end(b"A" * 20 + b" x", 0) == 20


class _CountingBytes(bytes):
    """Counts the bytes read through indexing and slicing."""

    reads = 0

    def __getitem__(self, key: Any) -> Any:
        got = super().__getitem__(key)
        type(self).reads += len(got) if isinstance(key, slice) else 1
        return got


def test_a_word_scan_stops_where_the_budget_does() -> None:
    _CountingBytes.reads = 0
    source = _CountingBytes(b"A'" + b"x" * 100_000)  # a quote left open to the end
    token = bc._REREAD_BUDGET.set([10])
    try:
        assert bc._shell_word_end(source, 0) is None
    finally:
        bc._REREAD_BUDGET.reset(token)
    assert _CountingBytes.reads <= 12


def test_a_substitution_the_budget_cannot_pay_is_not_scanned() -> None:
    command = "echo `cat <<'E'\n" + "x\n" * 1000 + "E\n`"
    root = bc._PARSER.parse(command.encode()).root_node
    ((sub, heredocs),) = bc._substituted_heredocs(root)
    _CountingBytes.reads = 0
    token = bc._REREAD_BUDGET.set([10])
    try:
        got = bc._substitution_rereads(
            sub, heredocs, _CountingBytes(command.encode()), Dialect.UNKNOWN
        )
    finally:
        bc._REREAD_BUDGET.reset(token)
    assert got == Verdict.ASK
    assert _CountingBytes.reads == 0


def test_the_budget_pays_exactly_what_it_holds() -> None:
    token = bc._REREAD_BUDGET.set([5])
    try:
        assert bc._charge_reread(5)
        assert not bc._charge_reread(1)
    finally:
        bc._REREAD_BUDGET.reset(token)


def test_the_end_line_search_stays_within_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex's family: no line ends any body, so each search would scan the rest of the source.

    On 61eda564, 1,600 of them scanned 26.9 MB for a 33.6 KB command (0.7 s).
    """

    command = "cat <<A''\nx\nB\nls\nA''\n" * 1600
    budget = bc._REREAD_PASSES * len(command) + bc._REREAD_SLACK
    scanned = [0]
    real = bc._shell_end_line

    def counting(source: bytes, begin: int, *a: Any, **k: Any) -> Any:
        got = real(source, begin, *a, **k)
        if got is None:
            scanned[0] += len(source) - begin
        elif got[0] >= 0:
            scanned[0] += got[1] + 1 - begin
        return got

    monkeypatch.setattr(bc, "_shell_end_line", counting)
    assert classify(command) == Verdict.ASK
    assert scanned[0] <= budget


# --- Fix round 2: code tree-sitter leaves as text ---
#
# tree-sitter-bash 0.25.1 parses a ``${…}`` operand holding a backtick
# substitution with no quote in it as a plain ``word``, and any operand after
# ``#``, ``%``, ``/``, ``^`` or ``,`` as a ``regex``, so no walk saw the
# substitution; it reads ``'…'`` there as literal, which inside double quotes
# it is not. Shell columns: ``<shell> -c`` with ``x`` unset, then with
# ``x=abc`` in the environment, on macOS (bash 3.2.57, zsh, dash, ksh93u+)
# and in Linux docker (bash 5.2.37, zsh, dash, ksh 93u+m, mksh); "all nine"
# is all of them (``.omc/probes/742/fix-r2/``).

_TICKS = f"`{_FIND}`"
# ALLOW on ce3cc695 and 80ff2cc2 bare and in double quotes; each deleted
# ``victim`` in the shells named, in at least one of the two runs.
_EXPANSION_OPERATORS = (
    (":-", "all nine, x unset"),
    ("-", "all nine, x unset"),
    (":?", "all but zsh, x unset"),
    ("?", "all but zsh, x unset"),
    (":+", "all nine, x set"),
    ("+", "all nine, x set"),
    ("#", "all nine, x set"),
    ("##", "all nine, x set"),
    ("%", "all nine, x set"),
    ("%%", "all nine, x set"),
    ("/", "all but dash, x set"),
    ("//", "all but dash, x set"),
    ("/#", "all but dash, x set"),
    ("/%", "all but dash, x set"),
    ("^", "Linux bash 5.2, x set"),
    (",,", "Linux bash 5.2, x set"),
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("quote", ["", '"'])
@pytest.mark.parametrize(("operator", "ran"), _EXPANSION_OPERATORS)
def test_a_backtick_substitution_the_parser_reads_as_text_is_refused(
    operator: str, ran: str, quote: str, dialect: Dialect
) -> None:
    command = f"echo {quote}${{x{operator}{_TICKS}}}{quote}"
    assert classify(command, dialect=dialect) == Verdict.DENY, (command, ran)


# ALLOW on ce3cc695; each deleted ``victim`` in the shells named.
_EXPANSION_TEXT_RUNS = (
    f"echo ${{x/a/{_TICKS}}}",  # the replacement: all but dash, x set
    f"echo ${{x:-${{y:-{_TICKS}}}}}",  # all nine
    f'echo "${{x:-${{y:-{_TICKS}}}}}"',  # all nine
    f"echo ${{x:- {_TICKS} }}",  # all nine
    f"[ ${{x:-{_TICKS}}} ]",  # all nine
    f"[[ ${{x:-{_TICKS}}} == y ]]",  # all nine
    f"echo ${{1:-{_TICKS}}}",  # all nine
    f"echo ${{@:-{_TICKS}}}",  # all nine
    f"echo ${{a[0]:-{_TICKS}}}",  # all but dash
    f"echo $(echo ${{x:-{_TICKS}}})",  # all nine
    f"cat <<EOF\n${{x:-{_TICKS}}}\nEOF",  # all nine
    f"echo ${{x:-`echo a; {_FIND}`}}",  # all nine
    f"echo ${{x:-`cat <<EOF\nx\nEOF\n{_FIND}\n`}}",  # all nine (the round-2 verify's form)
    # A ``regex`` operand hides a ``$(…)`` too: all nine with x set.
    f"echo ${{x#$({_FIND})}}",
    f"echo ${{x%%$({_FIND})}}",
    f'echo "${{x##$({_FIND})}}"',
    # And a backtick substitution with a quote in it: all nine with x set.
    "echo ${x#`find . -name 'victim' -delete`}",
    # ``'…'`` and ``$'…'`` inside double quotes: all nine (``$'…'`` all but mksh).
    f"echo \"${{x:-'$({_FIND})'}}\"",
    f"echo \"${{x:-'{_TICKS}'}}\"",
    f"echo \"${{x:+'$({_FIND})'}}\"",
    f"echo \"${{x:-$'$({_FIND})'}}\"",
    f"cat <<EOF\n${{x:-'$({_FIND})'}}\nEOF",
    f"echo \"${{x:-a${{y:-'$({_FIND})'}}}}\"",  # all nine, the inner one too
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _EXPANSION_TEXT_RUNS)
def test_code_in_a_parameter_operand_is_refused(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


# The cost of the rule: each asks (ALLOW on ce3cc695). No shell ran the
# first two, where ``'…'`` outside double quotes quotes.
_EXPANSION_TEXT_ASKS = (
    f"echo ${{x:-'{_TICKS}'}}",
    f"echo ${{x:-'$({_FIND})'}}",
    "echo ${x:-`pwd`}",
    'echo "${VERSION:-`cat VERSION`}"',
    "echo ${path#${HOME}/}",  # a ``${`` the parser left as text asks whatever it holds
    'echo "${path#${HOME}/}"',
    # The pattern is an argument, not a command, and ``'…'`` inside a parsed
    # ``$( )`` quotes: no shell ran ``find`` or ``rm``, and neither is refused.
    "echo ${x#rm -rf ~$(pwd)}",
    f"echo \"${{x:-$(echo '$({_FIND})')}}\"",  # ASK on 80ff2cc2 already
    "echo ${x#${y:=z}}",
    "echo ${x:-$[1+2]}",
    "echo ${x:-`cat <<EOF\nhi\nEOF\n`}",
    "echo `echo $(echo '`')`",  # a backtick in ``'…'`` inside backticks, below
    # All nine deleted ``victim``: tree-sitter leaves the first of the two
    # backslashes out of the word, so its re-read sees an escaped backtick.
    f"echo ${{x:-\\\\{_TICKS}}}",
)


@pytest.mark.parametrize("command", _EXPANSION_TEXT_ASKS)
def test_code_text_in_a_parameter_operand_asks(command: str) -> None:
    assert classify(command) == Verdict.ASK, command


def test_code_text_asks_whatever_its_reread_says(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ASK is the rule; the re-read only adds a DENY (``:`` itself asks today)."""

    monkeypatch.setattr(bc, "_reread", lambda *_a, **_k: Verdict.ALLOW)
    for command in ("echo ${x:-`pwd`}", 'echo "${path#${HOME}/}"'):
        assert classify(command) == Verdict.ASK, command


# Unchanged from ce3cc695 and 80ff2cc2: an escaped backtick is a literal one
# (no shell ran ``find``), a quoted here-document body is not expanded, and
# the parser reads ``$x``, ``$(…)`` and a backtick substitution with a quote
# in it in a ``:-`` operand itself.
_EXPANSION_TEXT_KEEPS = (
    ("echo ${x:-\\`" + _FIND + "\\`}", Verdict.ALLOW),
    ('echo "${x:-\\`' + _FIND + '\\`}"', Verdict.ALLOW),
    (f"cat <<'EOF'\n${{x:-{_TICKS}}}\nEOF", Verdict.ALLOW),
    ("echo ${x:-default} ${HOME:-/tmp} ${x#$HOME} ${f%.txt} ${p##*/} ${n//-/_}", Verdict.ALLOW),
    ("echo ${x:-$(pwd)} ${x:-`echo 'a'`}", Verdict.ASK),  # ASK on 80ff2cc2 already
    (f"echo ${{x:-$({_FIND})}}", Verdict.DENY),
    ("echo ${x:-`find . -name 'victim' -delete`}", Verdict.DENY),
)


@pytest.mark.parametrize(("command", "verdict"), _EXPANSION_TEXT_KEEPS)
def test_a_parameter_operand_the_parser_reads_keeps_its_verdict(
    command: str, verdict: Verdict
) -> None:
    assert classify(command) == verdict, command


# Inside backticks, bash and ksh93 end the substitution at the first unescaped
# backtick, inside ``'…'`` and ``$'…'`` too; tree-sitter reads the string
# across it. ALLOW on ce3cc695 unless noted; each of the first three deleted
# ``victim`` in macOS bash 3.2.57 and ksh93u+ and Linux bash 5.2.37 (zsh,
# dash, ksh 93u+m and mksh did not).
_BACKTICK_QUOTE_RUNS = (
    f"echo `echo '`; {_FIND}; echo `'`",
    f"echo `echo $'`; {_FIND}; echo `'`",
    f"x=`echo '`; {_FIND}; echo `'`",  # ASK on ce3cc695
    # A comment holding a backtick: all nine shells end the substitution there
    # and deleted ``victim``; tree-sitter reads the comment to the line's end.
    f"echo `echo hi # x`; {_FIND}; echo `y\n`",
    f"echo `echo hi # x`; {_FIND}; echo `\n`",
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _BACKTICK_QUOTE_RUNS)
def test_a_quote_the_backticks_cut_is_refused(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


@pytest.mark.parametrize(
    ("command", "verdict"),
    [
        # Inside ``$( )`` what the backticks end leaves a quote open: asked
        # (ALLOW on ce3cc695; the same three shells deleted ``victim``). #768
        # fix round 1 reads the split in the statement inside ``$( )``, where
        # no quote is left open, and refuses it.
        (f"echo $(echo `echo '`; {_FIND}; echo `'`)", Verdict.DENY),
        # An escaped backtick does not end it (no shell ran ``find``), and a
        # quote with no backtick in it is what most substitutions hold.
        (f"echo `echo '\\`; {_FIND}; echo \\`'`", Verdict.ALLOW),
        ("echo `git log --format='%h' -1`", Verdict.ALLOW),
        ("echo `date '+%F'`", Verdict.ALLOW),
        ("echo `echo '$HOME $(date)'`", Verdict.ALLOW),
        # Inside ``$( )`` the shells keep a quoted backtick (none ran anything).
        ("echo $(echo '`'; echo '`')", Verdict.ALLOW),
    ],
)
def test_a_quote_inside_backticks(command: str, verdict: Verdict) -> None:
    assert classify(command) == verdict, command


# --- Fix round 3: the round-3 verify's measured rules (``.omc/probes/742/verify-r3/``) ---

# A ``<(…)`` or ``>(…)`` in a ``${…}`` operand is a plain word to tree-sitter.
# ALLOW on 80ff2cc2 and 3963bea9 unless noted; each deleted ``victim`` in
# macOS bash 3.2.57 and Linux bash 5.2.37, and no other shell measured ran
# it (zsh, dash, ksh93u+, ksh 93u+m, mksh; Alpine bash 5.3.20 for the first).
_PROCESS_SUBSTITUTION_IN_OPERAND = (
    f"echo ${{x:-<({_FIND})}}",
    f"echo ${{x-<({_FIND})}}",
    f"echo ${{HOME:+<({_FIND})}}",
    f"echo ${{x:?<({_FIND})}}",
    f"echo ${{x:->({_FIND})}}",
    f"cat ${{x:-<({_FIND})}}",
    f"echo ${{x:- <({_FIND}) }}",
    f"echo ${{x:-${{y:-<({_FIND})}}}}",
    f"echo ${{x:-<({_FIND})}}x",
    f"x=1; echo ${{x:+<({_FIND})}}",  # ASK on 3963bea9
    f"echo ${{x:=<({_FIND})}}",  # ASK on 3963bea9
    f"x=${{y:-<({_FIND})}}",  # ASK on 3963bea9
)
# A word holding an escaped backtick inside backticks: the shells unescape it
# into a nested substitution. ALLOW on 80ff2cc2 and 3963bea9 unless noted;
# each deleted ``victim`` in all nine shells (bash 5.3.20 too for the first).
_ESCAPED_BACKTICK_RUNS = (
    f"echo `echo \\`{_FIND}\\``",
    f"cat `echo \\`{_FIND}\\``",
    f"ls `echo \\`{_FIND}\\``",
    f"echo `echo \\`{_FIND}\\` | cat`",
    f"echo ` echo \\` {_FIND} \\` `",
    f"echo `echo \\`echo \\\\\\`{_FIND}\\\\\\`\\``",
    f"echo `echo \\${{x:-\\`{_FIND}\\`}}`",
    f"x=`echo \\`{_FIND}\\``",  # ASK on 3963bea9
    f'echo "`echo \\`{_FIND}\\``"',  # ASK on 3963bea9
    f"echo `true; \\`{_FIND}\\``",  # ASK on 3963bea9
)


@pytest.mark.parametrize("dialect", _BASH_DIALECTS)
@pytest.mark.parametrize("command", _PROCESS_SUBSTITUTION_IN_OPERAND + _ESCAPED_BACKTICK_RUNS)
def test_code_the_round_3_verify_found_as_text_is_refused(command: str, dialect: Dialect) -> None:
    assert classify(command, dialect=dialect) == Verdict.DENY, command


@pytest.mark.parametrize(
    ("command", "verdict"),
    [
        # The cost: ALLOW on 80ff2cc2 and 3963bea9, and harmless. The first is
        # the one line of the 31,348-string corpus and the verify's 199 lines
        # whose verdict the two rules change.
        ("echo `basename \\`pwd\\``", Verdict.ASK),
        ("echo `echo \\`date\\``", Verdict.ASK),
        ("echo ${x:-<(echo hi)}", Verdict.ASK),
        # Unchanged: no shell ran ``find`` in these.
        (f"echo `echo \\$\\({_FIND}\\)`", Verdict.ALLOW),
        (f"echo $(echo \\`{_FIND}\\`)", Verdict.ALLOW),
        (f"echo ${{x:-=({_FIND})}}", Verdict.ALLOW),
        ("diff <(sort a) <(sort b)", Verdict.ALLOW),
        (f"echo ${{x:-\\<({_FIND})}}", Verdict.ASK),  # ASK on 80ff2cc2 already
        # Parsed as a process substitution, so walked already: DENY on 80ff2cc2.
        (f"echo --file=<({_FIND})", Verdict.DENY),
    ],
)
def test_the_round_3_rules_cost_and_keep(command: str, verdict: Verdict) -> None:
    assert classify(command) == verdict, command


def test_nested_parameter_operands_stay_fast() -> None:
    """The quoted context is carried down the walk, not found by climbing ``.parent``.

    py-tree-sitter answers ``.parent`` by walking down from the root, so the
    climb made nested operands cubic. Sixteen ``echo ${x:-${x:-…pwd…}}``
    chains 300 deep in one 29 KB line took 3.5 s on 3963bea9 and take 31 ms
    now, against 20 ms on 80ff2cc2 (macOS, Python 3.12, best of three; one
    run on 3963bea9; ``.omc/probes/742/finish/``). Not 490 deep as the
    verify measured: under pytest that depth reaches the recursion limit,
    which asks at once. The bound leaves room for a slow runner.
    """

    command = "; ".join(["echo " + "${x:-" * 300 + "pwd" + "}" * 300] * 16)
    started = time.perf_counter()
    assert classify(command) == Verdict.ALLOW
    assert time.perf_counter() - started < 1.0


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


# Ran with no dialog in the TUI under ``auto`` on b160b8f7 (the first, with
# zsh, in the issue's live run): refused now.
_GATE_DENY = (
    "cat <<A''\nx\nA\nfind . -name 'keep*' -delete\nA''",
    "cat <<EOF\nEOF \ncat <<'Q'\nEOF\nfind . -name 'keep*' -delete\nQ",
    # Fix round 1: R1 (live with zsh) and R2 (live with bash) ran with no
    # dialog on 61eda564 and deleted the files; refused now.
    "echo `cat <<'EOF'\nx\nEO\\\nF\nfind . -name 'keep*' -delete\nEOF\n`",
    "echo `cat <<'EOF'\n`\nfind . -name 'keep*' -delete\necho `echo\nEOF\n`",
    # Fix round 2: in the round-2 verify's TUI run the first ran with no
    # dialog on ce3cc695 with zsh and bash, the second with bash, and each
    # deleted both files; refused now.
    "echo ${x:-`rm -f keep1.txt keep2.txt`}",
    "echo `echo '`; find . -name 'keep*' -delete; echo `'`",
    # Fix round 3: in the round-3 verify's TUI run the first ran with no
    # dialog on 3963bea9 and 80ff2cc2 with bash, the second with bash and
    # zsh, and each deleted both files; refused now.
    "echo ${x:-<(rm -f keep1.txt keep2.txt)}",
    "echo `echo \\`rm -f keep1.txt keep2.txt\\``",
)
# Asked now: the shells read no end line, so nothing is re-read.
_GATE_ASK = ("cat <<EOF\nx\nEOF ",)
_GATE_READS = ("cat <<'EOF'\nx\nEOF", "cat <<EOF\nx\nEOF\n")


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/sh"])
async def test_auto_interactive_refuses_or_asks(
    shell: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_shell(monkeypatch, shell)
    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    for command in _GATE_DENY:
        ctx = _Ctx(has_ui=True)
        result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(result, ToolCallResult) and result.block, command
        assert ctx.ui.select_calls == 0, command  # refused, not asked
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
    """``headless_default="block"`` blocks the ASK too; ``-p`` (``"allow"``) runs it, as every ASK.

    ``-p`` running an ASK is ADR-0120's rule and pre-existing; the DENY is refused there.
    """

    _pin_shell(monkeypatch, "/bin/bash")
    block = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO), headless_default="block"
    )
    allow = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    ctx = _Ctx(has_ui=False)
    for command in (*_GATE_DENY, *_GATE_ASK):
        blocked = await block._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(blocked, ToolCallResult) and blocked.block, command
    for command in _GATE_DENY:
        blocked = await allow._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(blocked, ToolCallResult) and blocked.block, command
    for command in _GATE_READS:
        assert await block._on_tool_call(_event(command), ctx) is None, command  # type: ignore[arg-type]


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh", "/bin/ksh"])
def test_rpc_permissions_evaluate_matches_the_gate(
    shell: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_shell(monkeypatch, shell)
    for commands, expected in ((_GATE_DENY, "deny"), (_GATE_ASK, "ask"), (_GATE_READS, "allow")):
        for command in commands:
            got = evaluate_tool_call(PermissionMode.AUTO, "bash", {"command": command}, "/proj")
            assert got == expected, command
