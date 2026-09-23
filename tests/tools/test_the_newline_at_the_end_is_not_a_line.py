"""#309 — the empty element a trailing newline leaves behind is not a line.

``truncate_tail`` split on ``"\\n"`` raw, so output that ends in a newline
carried an extra final element. It costs 0 bytes, which
makes it the one line that fits a byte budget, and the real line above it was
dropped whole. MEASURED on the pre-fix tree through the model-facing bash tool
(``.omc/specs/309-measure.py``), ``python3 -c "print('x'*1000000)"``::

    TruncationInfo(truncated=True, truncated_by='bytes', original_lines=2,
                   kept_lines=1, original_bytes=1000001, kept_bytes=0)

— a megabyte run, and 0 bytes of it in the 133 bytes the model received.

Every ``print``, every ``echo``, every file that ends the way a POSIX file is
supposed to end takes that path, so the tests here are written at the two
levels the defect is visible from: the helper's own counts, and what the tools
that call it hand back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.cli.repl import _cap_for_the_record
from aelix_coding_agent.tools import create_bash_tool, create_read_tool
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    truncate_head,
    truncate_tail,
)

CAP = DEFAULT_MAX_BYTES  # 50KB
HUGE = "x" * 60000


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t309"))


def _body(text: str) -> str:
    """The recorded/returned text without its bracketed notice."""

    return text.split("\n\n[", 1)[0]


# --- the counting rule ------------------------------------------------------

#: (label, text, the number of lines a reader counts).
#:
#: The right column is ``wc -l`` for text that ends in a newline and ``wc -l``
#: + 1 for text whose last line is unterminated — the two cases a line-counting
#: rule has to answer, stated as the numbers themselves so the rule cannot be
#: re-derived from the implementation it is checking.
_COUNTS: list[tuple[str, str, int]] = [
    ("empty", "", 0),
    ("one newline", "\n", 1),
    ("one unterminated line", "a", 1),
    ("one terminated line", "a\n", 1),
    ("a blank line under it", "a\n\n", 2),
    ("three blank lines", "\n\n\n", 3),
    ("two lines, terminated", "a\nb\n", 2),
    ("two lines, unterminated", "a\nb", 2),
    ("a blank line between", "a\n\nb\n", 3),
]


@pytest.mark.parametrize(
    ("label", "text", "expected"), _COUNTS, ids=[r[0] for r in _COUNTS]
)
def test_a_line_terminator_ends_a_line_and_does_not_begin_one(
    label: str, text: str, expected: int
) -> None:
    """Exactly ONE trailing element is dropped, because one terminator makes it.

    Dropping the whole run instead would say ``"a\\n\\n"`` is one line, and a
    reader who counted it — or ``wc -l`` — says two. The counts are asserted
    through both public entry points, which is where every caller reads them.
    """

    _, tail = truncate_tail(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
    _, head = truncate_head(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
    assert tail.original_lines == expected, label
    assert head.original_lines == expected, label
    # Nothing here is over either cap, so the text comes back untouched.
    assert tail.kept_lines == expected and head.kept_lines == expected, label


def test_the_counting_split_is_the_shared_one() -> None:
    """The rule has a name, and both directions ask it the same question.

    Imported inside the test on purpose: everything else in this file is
    written against surfaces that exist on the pre-fix tree, so the file
    collects there and its red/green can be measured test by test. This one
    cannot — the function is new — and it says so by failing to import.
    """

    from aelix_coding_agent.tools._truncate import split_lines_for_counting

    assert split_lines_for_counting("") == []
    assert split_lines_for_counting("\n") == [""]
    assert split_lines_for_counting("a\n") == ["a"]
    assert split_lines_for_counting("a\n\n") == ["a", ""]
    assert split_lines_for_counting("a\nb") == ["a", "b"]


# --- truncate_tail ----------------------------------------------------------


def test_a_trailing_newline_no_longer_takes_the_line_above_it() -> None:
    """The issue, at the helper. Pre-fix: ``kept_bytes=0``, body empty."""

    body, info = truncate_tail(HUGE + "\n", max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)

    assert body == "x" * CAP
    assert info.truncated is True
    assert info.truncated_by == "bytes"
    assert info.last_line_partial is True
    assert (info.original_lines, info.kept_lines) == (1, 1)
    assert (info.original_bytes, info.kept_bytes) == (60001, CAP)


def test_a_blank_last_line_is_a_line_and_still_not_an_answer() -> None:
    """``print(big); print()`` — a blank line fits any budget, and buys nothing.

    Pre-fix the body was the single newline between the two lines: 1 byte of a
    60,002-byte output. The blank line is real and is kept; what changes is
    that it no longer displaces the line it sits under.
    """

    body, info = truncate_tail(
        HUGE + "\n\n", max_lines=DEFAULT_MAX_LINES, max_bytes=CAP
    )

    assert body == "x" * (CAP - 1) + "\n"
    assert len(body.encode()) == CAP
    assert info.last_line_partial is True
    assert (info.original_lines, info.kept_lines) == (2, 2)
    # The fragment opens the body, so it came from line
    # ``original_lines - kept_lines + 1`` — line 1, the long one, NOT the
    # blank line 2 that ends the output.
    assert info.original_lines - info.kept_lines + 1 == 1


def test_blank_lines_that_are_the_whole_tail_are_still_the_tail() -> None:
    """The fallback must not fire when nothing was dropped for the blanks.

    2001 blank lines over a 2000-line cap: the LINE cap binds, no byte cap is
    reached, and the honest tail of an output that is nothing but blank lines
    is blank lines. Reaching for a fragment here would invent one.
    """

    body, info = truncate_tail("\n" * 2001, max_lines=2000, max_bytes=CAP)

    assert body == "\n" * 1999
    assert info.truncated_by == "lines"
    assert info.last_line_partial is False
    assert (info.original_lines, info.kept_lines) == (2001, 2000)


def test_the_line_cap_keeps_the_last_line_the_command_printed() -> None:
    """A newline-terminated 100 lines under a 5-line cap keeps 96-100.

    Pre-fix it kept 97-100 and the phantom, and the notice built from those
    counts said "lines 97-101 of 101" about output with 100 lines in it.
    """

    text = "".join(f"{n}\n" for n in range(1, 101))
    body, info = truncate_tail(text, max_lines=5, max_bytes=CAP)

    assert body == "96\n97\n98\n99\n100"
    assert (info.original_lines, info.kept_lines) == (100, 5)
    assert info.original_lines - info.kept_lines + 1 == 96


def test_only_the_terminator_over_the_cap_is_not_a_truncation() -> None:
    """``"x" * 50KB + "\\n"``: every line arrives, so nothing is announced.

    The byte is dropped rather than restored — a body already at the cap has
    no room for a line ending that carries nothing — and it is still COUNTED,
    which is what stops a record one byte over the cap from answering "fits".
    Pre-fix this returned an EMPTY body and called it a byte truncation.
    """

    for truncate in (truncate_tail, truncate_head):
        body, info = truncate("x" * CAP + "\n", max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
        assert body == "x" * CAP
        assert info.truncated is False
        assert info.truncated_by is None
        assert info.original_bytes == CAP + 1
        assert info.kept_bytes == CAP


def test_a_content_byte_over_the_cap_is_still_a_truncation() -> None:
    """The other half of that rule: the byte over the cap has to BE the newline.

    One byte over with no terminator to blame is an ordinary byte truncation —
    announced, body cut to fit. This is the case the "not a truncation" branch
    must NOT claim, and only ``and text.endswith("\\n")`` in
    ``_fits_but_for_the_terminator`` keeps it out: delete that one clause and
    both directions take the branch here, hand back ``text[:-1]`` and report
    ``truncated=False, truncated_by=None`` — the last character gone with no
    notice and no detail saying so.

    MEASURED with the clause deleted (whole suite green, 26/26 here green
    before this test existed)::

        truncate_tail("x"*51200 + "x", 2000, 51200)
        -> body 51200B, truncated=False, original_bytes=51201, kept_bytes=51200

    The multibyte row is the same hole one step worse: ``text[:-1]`` slices
    CHARACTERS, so it drops all three bytes of the ``한`` while ``kept_bytes``
    is computed as ``original_bytes - 1`` regardless — a body of 51,198 bytes
    reported as 51,200.
    """

    for truncate in (truncate_tail, truncate_head):
        for label, text in (
            ("ascii", "x" * CAP + "x"),
            ("multibyte", "x" * (CAP - 2) + "한"),
        ):
            body, info = truncate(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
            assert len(text.encode()) == CAP + 1, label  # one byte over, no newline
            assert info.truncated is True, label
            assert info.truncated_by == "bytes", label
            assert info.original_bytes == CAP + 1, label
            assert info.kept_bytes == len(body.encode()), label
            assert info.kept_bytes <= CAP, label
            assert "�" not in body, label


def test_a_zero_byte_budget_buys_nothing() -> None:
    """``max_bytes=0`` keeps nothing — the one input where a slice lies.

    No caller passes 0: read/grep/find/ls pass ``DEFAULT_MAX_BYTES``, the RPC
    one 32KB, and ``bash``'s is the only configurable one — supplied by tests
    alone, since ``cli/entry.py`` ``_tool_options_from_env`` sets timeouts and
    never ``max_bytes``. So this is here for the expression rather than the
    caller: the tail fallback slices ``joined[max(len(joined) - max_bytes, 0):]``
    and the obvious ``joined[-max_bytes:]`` is NOT the same function —
    ``[-0:]`` is the WHOLE string, so a budget of nothing would hand back
    everything and call it capped. Measured: ``b"abcdef"[-0:] == b"abcdef"``.
    """

    for truncate in (truncate_tail, truncate_head):
        body, info = truncate("abcdef", max_lines=1, max_bytes=0)
        assert body == ""
        assert info.kept_bytes == 0
        assert info.truncated is True


def test_text_that_fits_whole_is_returned_whole() -> None:
    """Including its terminator: the untruncated path is still byte-exact."""

    for text in ("hi\n", "hi", "", "\n", "a\n\n\n"):
        for truncate in (truncate_tail, truncate_head):
            body, info = truncate(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
            assert body == text
            assert info.truncated is False
            assert info.kept_bytes == info.original_bytes == len(text.encode())


def test_a_multibyte_char_is_never_cut_in_half() -> None:
    """The fragment path still decodes char-safe, trailing newline or not."""

    text = "한" * 20000 + "\n"  # 3 bytes each
    body, info = truncate_tail(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)

    assert "�" not in body
    assert set(body) == {"한"}
    assert info.last_line_partial is True
    assert info.kept_bytes <= CAP


# --- truncate_head ----------------------------------------------------------


def test_the_head_of_a_file_that_ends_the_way_files_do() -> None:
    """Exactly ``max_lines`` terminated lines is not a truncation.

    Pre-fix the trailing newline made it ``max_lines + 1`` lines, so the cap
    "bound", the body came back without its final newline and the caller was
    handed a continuation offset with nothing behind it.
    """

    text = "a\n" * DEFAULT_MAX_LINES
    body, info = truncate_head(text, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)

    assert body == text
    assert info.truncated is False
    assert (info.original_lines, info.kept_lines) == (2000, 2000)

    body, info = truncate_head("a\n" * 2001, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)
    assert info.truncated is True
    assert (info.original_lines, info.kept_lines) == (2001, 2000)


def test_the_head_keeps_its_empty_body_when_nothing_whole_fits() -> None:
    """The head does NOT mirror the tail's fragment fallback, on purpose.

    A blank first line starves the byte budget exactly as a blank last line
    does, but a head truncation hands the caller somewhere to continue from
    and a tail truncation is the end of the output. ``read`` turns this empty
    body into "[Showing lines 1-1 … Use offset=2 to continue.]", and at
    offset 2 its first-line-exceeds-the-limit branch names the line and how to
    read it. A fragment here would answer with 50KB of a line the next offset
    then skips.
    """

    body, info = truncate_head("\n" + HUGE, max_lines=DEFAULT_MAX_LINES, max_bytes=CAP)

    assert body == ""
    assert info.truncated_by == "bytes"
    assert (info.original_lines, info.kept_lines) == (2, 1)


# --- the callers ------------------------------------------------------------


async def test_the_bash_tool_hands_the_model_the_tail_of_what_it_ran(tmp_path):
    """A command that ends its output in a newline used to lose the whole body.

    The caps are small so the test is small; the shape is the issue's own —
    one line longer than the byte budget, terminated. MEASURED pre-fix, the
    model got a body of 0 bytes under "[Showing lines 2-2 of 2 (16B limit).
    Full output: …]" for a command that printed 31 characters. Not the
    literal ``(no output)``: the notice is appended to the empty body before
    ``formatOutput`` chooses that placeholder, so it never fires on this
    path — asserted below because that is the only thing the placeholder's
    absence proves.

    ``printf '%s\\n'`` and not ``echo``, which is what this ran until both
    windows legs of CI run 35754271472 went red on it. ``echo`` there is
    PowerShell's ``Write-Output``, which terminates CRLF, and that made the
    last 16 bytes ``"a" * 15 + "\\r"``. PowerShell because the run says so:
    ``_resolve_shell_win32`` resolves ``$SHELL`` → pwsh → powershell →
    ``%COMSPEC%`` (``tools/bash.py:150``), and of those only PowerShell both
    expands the ``$(seq 1 100)`` a test below passed on AND writes CRLF from
    ``echo`` — bash would have given ``echo`` an LF, ``cmd.exe`` would not have
    expanded the ``$(…)``. ``printf`` is a different thing entirely: Git's
    ``usr/bin`` binary, on PATH because that image puts it there, writing the
    terminator it is handed. The terminator is this test's FIXTURE and not its
    subject — the defect is the newline, not which command emits it — so the
    fixture states one terminator rather than the platform's, and the CRLF
    shape gets a test of its own below, where it can be pinned without being
    called correct.
    """

    tool = create_bash_tool(str(tmp_path), {"max_bytes": 16, "max_lines": 999})
    result = await _exec(tool, {"command": "printf '%s\\n' " + "a" * 31})

    text = result.content[0].text
    assert _body(text) == "a" * 16
    assert "(no output)" not in text
    # The notice names the line the fragment came from and how big it was.
    # ``rsplit("\\n", 1)[-1]`` — pi's getLastLineBytes — is the empty string
    # for every output that ends in a newline. Pre-fix that never showed,
    # because this branch could not be reached for such an output at all;
    # reaching it without changing the source of the number would print
    # "(line is 0B)" here.
    assert "[Showing last 16B of line 1 (line is 31B)." in text
    assert result.details.truncation.last_line_partial is True
    assert result.details.truncation.kept_bytes == 16


async def test_a_crlf_line_still_reaches_the_model_as_its_own_tail(tmp_path):
    """The windows shape of the test above, pinned as an INVARIANT.

    MEASURED on darwin by emitting the windows bytes directly rather than
    claiming a platform this box does not have — ``printf '%s\\r\\n'`` with 31
    ``a``\\ s is byte for byte what PowerShell's ``echo`` wrote on the windows
    legs of run 35754271472::

        raw             b"a" * 31 + b"\\r\\n"
        counted lines   ["a" * 31 + "\\r"]   — ONE line, 32 bytes
        body            "a" * 15 + "\\r"     — 16 bytes
        notice          "[Showing last 16B of line 1 (line is 32B)."

    and ``'aaaaaaaaaaaaaaa\\r' == 'aaaaaaaaaaaaaaaa'`` is that CI failure.

    The ``"\\r"`` reaches the body for two reasons, and only the second is
    arguable. ``split_lines_for_counting`` pops one ``"\\n"`` and nothing else,
    which is deliberate in the HELPER — it is ``read``'s helper too, and
    ``read`` has to hand back the bytes it was given (#317 says so in as many
    words). What has no such defence is that the model-facing tool, unlike the
    ``!cmd`` record, has nothing in front of it that folds the trailing run:
    ``_normalise_trailing_terminators`` (``cli/repl.py:138``) exists for
    exactly this byte — written when the windows legs recorded ``51199 ==
    51200`` (run 35522838940) — and it argues that a terminator at the very
    end is the platform's choice and not the command's. By that argument these
    16 bytes are not an answer this test may write down: close the asymmetry
    and they become ``"a" * 16``.

    It is also NOT the ``huge + LF + CR`` row of this commit's RESIDUAL table,
    which the CI failure was first read as. That row needs TWO lines — a short
    last one that fits and a long one above it dropped whole to make room for
    it. Here there is ONE line, nothing is dropped for the CR, and the fragment
    fallback hands back its tail exactly as designed.

    So what is asserted is the claim #309 owns and every reading above agrees
    on: the model gets the TAIL OF THE LONG LINE where pre-fix it got 0 bytes
    of it — a spent budget, all of it from the line the command printed, at
    most the one byte of terminator debris. The notice is pinned by shape, that
    it names line 1 and gives that line a size, and not by the 32.
    """

    tool = create_bash_tool(str(tmp_path), {"max_bytes": 16, "max_lines": 999})
    result = await _exec(tool, {"command": "printf '%s\\r\\n' " + "a" * 31})

    body = _body(result.content[0].text)
    assert len(body.encode()) == 16
    assert body.startswith("a" * 15)
    assert body.lstrip("a") in ("", "\r")
    assert "[Showing last 16B of line 1 (line is " in result.content[0].text
    info = result.details.truncation
    assert info.last_line_partial is True
    assert (info.original_lines, info.kept_lines) == (1, 1)


async def test_the_bash_notice_names_the_line_the_fragment_came_from(tmp_path):
    """``print(big); print()`` at the model-facing tool, in miniature.

    The blank line is the LAST line, so sizing the notice from the last line
    sizes it from the empty one. MEASURED by making
    ``partial_index`` ``original_lines - 1``: "[Showing last 16B of line 1
    (line is 0B)]" over 16 bytes of a line that is 31. The fragment opens the
    body, so the line it came from is the FIRST kept one —
    ``original_lines - kept_lines``.
    """

    tool = create_bash_tool(str(tmp_path), {"max_bytes": 16, "max_lines": 999})
    result = await _exec(tool, {"command": "printf '%s\\n\\n' " + "a" * 31})

    text = result.content[0].text
    assert _body(text) == "a" * 15 + "\n"
    assert "[Showing last 16B of line 1 (line is 31B)." in text
    assert result.details.truncation.kept_lines == 2


async def test_the_bash_line_notice_counts_the_lines_the_command_printed(tmp_path):
    """100 printed lines under a 5-line cap: lines 96-100 of 100.

    Pre-fix: "[Showing lines 97-101 of 101]" — a range whose last line is the
    newline that ended line 100, and a body missing line 96 to make room for
    it.
    """

    tool = create_bash_tool(str(tmp_path), {"max_lines": 5})
    result = await _exec(tool, {"command": "printf '%s\\n' $(seq 1 100)"})

    text = result.content[0].text
    path = result.details.full_output_path
    assert f"\n\n[Showing lines 96-100 of 100. Full output: {path}]" in text
    assert _body(text) == "96\n97\n98\n99\n100"


async def test_read_does_not_offer_an_offset_with_nothing_behind_it(tmp_path):
    """A file of exactly 2000 lines is read whole and said to be read whole.

    Pre-fix: "[Showing lines 1-2000 of 2001. Use offset=2001 to continue.]" —
    and offset 2001 is the empty element, so the model that followed it read
    nothing and was told nothing was there.

    Written in BINARY, and checked against the file's BYTES, because this read
    three different files under three different rules until the windows legs of
    CI run 35754271472 caught it: ``Path.write_text`` translates ``"\\n"`` to
    the platform's ending, so the fixture was silently the CRLF one there;
    ``read`` decodes what is on disk and translates nothing, so it returned
    that CRLF; and ``Path.read_text`` reads the same file back under universal
    newlines, which translates it to ``"\\n"`` again. ``'line 1\\r\\nli...' ==
    'line 1\\nline...'`` was those last two disagreeing about a file ``read``
    had handed back correctly. The tool's contract is the file's bytes, so the
    bytes are what it is held to.

    Both terminators now run everywhere, since the rule under test is about
    the ``"\\n"`` and a windows file puts a ``"\\r"`` in front of it — 2000
    lines either way, and MEASURED on darwin 18,893B and 20,893B, both well
    under the 50KB cap, so it is the LINE cap both rows sit exactly on.
    """

    tool = create_read_tool(str(tmp_path))

    for label, newline in (("lf", "\n"), ("crlf", "\r\n")):
        path = Path(tmp_path) / f"exactly-2000-{label}.txt"
        path.write_bytes(
            "".join(f"line {n}{newline}" for n in range(1, DEFAULT_MAX_LINES + 1)).encode()
        )

        result = await _exec(tool, {"path": str(path)})

        text = result.content[0].text
        assert text == path.read_bytes().decode(), label
        assert "[Showing lines" not in text, label
        assert "continue" not in text, label


# --- the `!cmd` record (#299), now that its workaround is gone --------------


async def test_the_record_keeps_what_299_pinned_without_its_own_cap():
    """The nine rows of ``tests/cli/test_user_bash_reaches_the_model.py`` are
    the contract; these are the two claims the removal could have moved.

    #299 capped the record by splitting the trailing terminator run off,
    reserving budget for the blank lines and counting the whole itself, all of
    it to keep this helper's phantom line away from the body. That is gone.
    What the record must still do: carry the tail when the output is over the
    cap, and say which line the fragment came from.
    """

    record = _cap_for_the_record(HUGE + "\n\n")

    assert record.body == "x" * (CAP - 1) + "\n"
    assert "[Showing the last 50.0KB of line 1 (50.0KB limit)." in record.notice
    assert record.truncation is not None
    assert record.truncation["original_lines"] == 2
    assert record.truncation["kept_lines"] == 2


async def test_the_record_reports_the_bytes_the_command_printed():
    """A trailing CRLF is folded for the BODY and not for the count.

    The body is normalised so a windows child and a POSIX child record the
    same thing (#299's windows CI failure). The byte count is the command's,
    because "it printed this much, the record keeps this much" is what a
    reader of the record is being told.
    """

    record = _cap_for_the_record(HUGE + "\r\n")

    assert record.body == "x" * CAP
    assert record.truncation is not None
    assert record.truncation["original_bytes"] == len((HUGE + "\r\n").encode())
    assert record.truncation["kept_bytes"] == CAP


async def test_an_output_at_the_cap_plus_a_newline_is_recorded_without_a_notice():
    """The row that the first cut of #299 let through at 51,201 bytes.

    It now comes out of the helper rather than out of this writer's own
    arithmetic, and it still has to come out the same: whole, capped, silent.
    """

    record = _cap_for_the_record("x" * CAP + "\n")

    assert record.body == "x" * CAP
    assert record.notice == ""
    assert record.truncation is None
