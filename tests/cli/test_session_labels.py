"""The single resume-label format, gated on real JSONL bytes.

Both resume pickers used to render ``{created} · {short-id}`` from two separate
copies of the same function, and the ``/resume`` copy had no tests at all. These
exercise :mod:`aelix_coding_agent.cli.session_labels` against session files
written to disk rather than against doubles, because every interesting failure
in it is a parsing failure: a prefilter that admits the wrong line, a truncation
that counts codepoints instead of terminal cells, a tail window that cuts a
record in half.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from aelix_agent_core.session.storage import JsonlSessionMetadata
from aelix_coding_agent.cli.session_labels import (
    by_recent_activity,
    first_user_message,
    format_age,
    last_activity,
    last_user_message,
    session_age,
    session_choice_label,
    session_detail_lines,
    short_field,
    truncate_cells,
)
from prompt_toolkit.utils import get_cwidth

_MINUTE = 60.0
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


def _header(session_id: str = "abcdef1234", stamp: str = "2026-08-01T09:00:00.000Z") -> str:
    return json.dumps(
        {"type": "session", "version": 3, "id": session_id, "timestamp": stamp, "cwd": "/w"}
    )


def _user(text: str, stamp: str = "2026-08-01T09:01:00.000Z") -> str:
    return json.dumps(
        {
            "id": "e1",
            "parentId": None,
            "timestamp": stamp,
            "message": {"content": [{"text": text, "type": "text"}], "role": "user"},
            "type": "message",
        }
    )


def _assistant(text: str, stamp: str = "2026-08-01T09:02:00.000Z") -> str:
    return json.dumps(
        {
            "id": "e2",
            "parentId": "e1",
            "timestamp": stamp,
            "message": {"content": [{"text": text, "type": "text"}], "role": "assistant"},
            "type": "message",
        }
    )


def _write(tmp_path: Path, *lines: str, name: str = "s.jsonl") -> str:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _meta(path: str, *, id_: str = "abcdef1234", created: str = "2026-08-01T09:00:00") -> object:
    return JsonlSessionMetadata(id=id_, created_at=created, cwd="/w", path=path)


# === format_age: a verbatim port of pi formatSessionDate ====================


def test_format_age_matches_pi_thresholds() -> None:
    """pi ``session-selector.js:22-40``, boundary by boundary.

    The boundaries are the whole content of this function — an off-by-one at any
    of them shows a plausible-looking wrong age, which is worse than no age.
    """

    now = 1_000_000_000.0
    table = [
        (0.0, "now"),
        (59.0, "now"),
        (_MINUTE, "1m"),
        (59 * _MINUTE, "59m"),
        (_HOUR, "1h"),
        (23 * _HOUR, "23h"),
        (_DAY, "1d"),
        (6 * _DAY, "6d"),
        (7 * _DAY, "1w"),
        (29 * _DAY, "4w"),
        (30 * _DAY, "1mo"),
        # 364 // 30 == 12, so the last month before a year reads "12mo" and not
        # "11mo". That is pi's arithmetic (``Math.floor(diffDays / 30)``), kept
        # rather than corrected: parity beats tidiness on a shared format.
        (364 * _DAY, "12mo"),
        (365 * _DAY, "1y"),
    ]
    for ago, expected in table:
        assert format_age(now - ago, now) == expected, f"{ago}s ago"


def test_format_age_never_renders_a_negative_age() -> None:
    """A session stamped in the future (clock skew, a copied file) reads ``now``."""

    now = 1_000_000_000.0
    assert format_age(now + 5 * _DAY, now) == "now"


def test_format_age_unknown_is_a_question_mark() -> None:
    assert format_age(None, 1_000_000_000.0) == "?"


# === first / last user message ==============================================


def test_first_user_message_is_the_first_not_the_last(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _header(),
        _user("the task"),
        _assistant("working"),
        _user("a short follow-up"),
    )
    assert first_user_message(path) == "the task"
    assert last_user_message(path) == "a short follow-up"


def test_the_user_substring_prefilter_does_not_admit_an_assistant_turn(
    tmp_path: Path,
) -> None:
    """The cheap ``b'"user"'`` scan decides what to PARSE, never what to return.

    The line below is an assistant turn carrying a tool call whose ARGUMENTS
    contain ``"role": "user"`` — which is not contrived, it is what every session
    in which the agent edits code that mentions message roles looks like,
    including the ones that produced this file. The bytes ``"user"`` appear
    unescaped, so the prefilter admits the line and only the structural role
    check keeps the assistant's text out of the label.

    The obvious fixture — an assistant that says the word ``"user"`` in its prose
    — does NOT test this: ``json.dumps`` escapes those quotes to ``\\"user\\"``,
    the prefilter misses the line, and the test passes with the role check
    deleted. It was written that way first and a sabotage run caught it.
    """

    assistant_with_a_role_argument = json.dumps(
        {
            "id": "e2",
            "timestamp": "2026-08-01T09:02:00.000Z",
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "setting the role field"},
                    {"type": "tool_call", "arguments": {"role": "user"}},
                ],
            },
        }
    )
    assert '"user"' in assistant_with_a_role_argument  # the prefilter WILL match
    path = _write(
        tmp_path,
        _header(),
        assistant_with_a_role_argument,
        _user("the real first turn"),
    )
    assert first_user_message(path) == "the real first turn"
    assert last_user_message(path) == "the real first turn"


def test_a_role_written_without_a_space_still_matches(tmp_path: Path) -> None:
    """pi writes ``"role":"user"``; this repo writes ``"role": "user"``.

    Both round-trip through the same session format (``jsonl_storage``'s docstring
    calls the on-disk format identical), so the scan must not depend on the JSON
    separator style of whichever agent wrote the file.
    """

    line = '{"id":"e1","timestamp":"2026-08-01T09:01:00Z","type":"message",'
    line += '"message":{"role":"user","content":[{"type":"text","text":"pi wrote this"}]}}'
    path = _write(tmp_path, _header(), line)
    assert first_user_message(path) == "pi wrote this"


def test_a_session_with_no_user_turn_has_no_message(tmp_path: Path) -> None:
    path = _write(tmp_path, _header(), _assistant("nobody asked"))
    assert first_user_message(path) is None
    assert last_user_message(path) is None


def test_a_missing_file_degrades_and_never_raises(tmp_path: Path) -> None:
    missing = str(tmp_path / "gone.jsonl")
    assert first_user_message(missing) is None
    assert last_user_message(missing) is None
    assert last_activity(missing) is None


def test_a_record_bigger_than_its_window_is_read_rather_than_given_up_on(
    tmp_path: Path,
) -> None:
    """Both windows had a one-byte cliff, and both failed toward a WRONG answer.

    ``last_activity``: ``_read_tail`` drops everything before its first newline so
    it never parses half a record, and when the last record is LARGER than the
    window the only newline in the blob is the file's own trailing one — so the
    slice was empty. MEASURED, a final record of 131 070 bytes yielded its
    timestamp and 131 071 yielded none, against a 131 072-byte window. That is not
    a neutral failure: the age then falls back to ``created_at``, which is biased
    STALE and silent about it, so a session created 300 days ago and used four
    minutes ago read ``10mo`` rather than the ``?`` ``format_age`` reserves for an
    unknown age. One large ``read`` result inside the last assistant turn is enough.

    ``first_user_message``: the same shape at the head. MEASURED, the cliff is at
    65 259 bytes of message text against a 65 536-byte window, and past it the row
    rendered ``(no messages)`` — byte-identical to a genuinely empty session, so
    the folder's LONGEST question displayed as no question at all.

    The last assertion is the control that keeps the fix honest: an actually
    empty session must still read ``(no messages)``.
    """

    def _session(name: str, *, first: str, pad: int) -> str:
        return _write(
            tmp_path,
            _header(),
            _user(first),
            json.dumps(
                {
                    "id": "e2",
                    "timestamp": "2026-08-02T11:00:00.000Z",
                    "type": "message",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "A" * pad}]},
                }
            ),
            name=name,
        )

    # A final record either side of the tail window, and far past it.
    for pad in (2_000, 130_000, 140_000, 400_000):
        path = _session(f"tail-{pad}.jsonl", first="the task", pad=pad)
        assert last_activity(path) == "2026-08-02T11:00:00.000Z", pad

    # A first message either side of the head window, and far past it.
    for size in (1_000, 60_000, 70_000, 200_000):
        opening = "refactor the payment module " + "A" * size
        path = _write(tmp_path, _header(), _user(opening), name=f"head-{size}.jsonl")
        got = first_user_message(path)
        assert got is not None and got.startswith("refactor the payment module"), size

    # Control: an empty session is still empty, and still says so.
    empty = _write(tmp_path, _header(), name="empty.jsonl")
    assert first_user_message(empty) is None
    assert "(no messages)" in session_choice_label(_meta(empty), 1e9, width=78)


def test_the_pickers_order_by_the_age_they_display(tmp_path: Path) -> None:
    """The label says "last used"; the list was sorted by "created".

    ``JsonlSessionRepo.list`` orders by ``created_at``. Every row these pickers
    print is labelled with last-ACTIVITY age. The two disagreed harmlessly while
    the startup menu printed every session, and stopped being harmless the moment
    a twenty-row cap stood in front of it: MEASURED over 25 sessions where one was
    created 300 days ago and used minutes ago, it sat at position 25 of 25, was
    cut, and the footer described it as "older".

    Twenty-five sessions here, not three, because the defect is the interaction
    with the cap and a fixture smaller than the cap cannot show it.
    """

    from aelix_coding_agent.cli.entry import _RESUME_MENU_LIMIT

    metas: list[object] = []
    for index in range(24):
        day = 24 - index
        stamp = f"2026-07-{day:02d}T12:00:00"
        path = _write(
            tmp_path,
            _header(stamp=stamp + ".000Z"),
            _user(f"filler {index}"),
            name=f"filler-{index:02d}.jsonl",
        )
        metas.append(_meta(path, id_="bbbbbbbb", created=stamp))

    active = _write(
        tmp_path,
        _header(stamp="2025-10-21T12:00:00.000Z"),
        _user("OLD-BUT-ACTIVE: the long refactor"),
        json.dumps(
            {
                "id": "e2",
                "timestamp": "2026-08-01T09:30:00.000Z",
                "type": "message",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            }
        ),
        name="active.jsonl",
    )
    metas.append(_meta(active, id_="aaaaaaaa", created="2025-10-21T12:00:00"))

    # The premise: in the repository's own order it is last, and the cap cuts it.
    assert active not in [getattr(m, "path", "") for m in metas[:_RESUME_MENU_LIMIT]]

    ranked = by_recent_activity(metas)
    assert getattr(ranked[0], "path", "") == active, [getattr(m, "path", "") for m in ranked[:3]]
    assert active in [getattr(m, "path", "") for m in ranked[:_RESUME_MENU_LIMIT]]
    # Nothing was lost or duplicated by the sort.
    assert sorted(getattr(m, "path", "") for m in ranked) == sorted(
        getattr(m, "path", "") for m in metas
    )


def test_one_malformed_record_costs_one_row_and_not_the_whole_session_list(
    tmp_path: Path,
) -> None:
    """A wrongly-TYPED field inside a well-formed record, which is the gap.

    Every shape here was guarded type by type — the record is checked, the message
    is checked, the block is checked — and the ``text`` VALUE was taken on trust.
    A block of ``{"type": "text", "text": 123}`` therefore reached ``str.join``
    and raised ``TypeError``, and because the pickers call this in a LOOP over
    every session, one malformed record in one file took away the user's entire
    session list, the good sessions with it — at exactly the moment they were
    trying to recover work.

    A session file is appended to by an agent process that can be killed
    mid-write, and it can arrive with a cloned repository, so "we wrote it, so the
    shape is ours" is not available to this module.

    The last block asserts the blanket, not just the one type: an object whose
    metadata raises on attribute access must still produce a row.
    """

    good = _write(tmp_path, _header(), _user("the good session"), name="good.jsonl")
    bad = _write(
        tmp_path,
        _header(),
        json.dumps(
            {
                "id": "e1",
                "timestamp": "2026-08-01T09:01:00.000Z",
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": 123}]},
            }
        ),
        name="bad.jsonl",
    )

    assert first_user_message(bad) is None
    # The good row survives the bad one — the property the loop actually needs.
    assert "the good session" in session_choice_label(_meta(good), 1e9, width=78)
    assert "(no messages)" in session_choice_label(_meta(bad), 1e9, width=78)

    class _Hostile:
        @property
        def path(self) -> str:
            raise RuntimeError("metadata that fights back")

        created_at = "2026-08-01T09:00:00"
        id = "abcdef1234"

    label = session_choice_label(_Hostile(), 1e9, width=78)
    assert "(no messages)" in label, label
    assert "\n" not in label


def test_control_characters_are_collapsed_so_a_row_stays_one_row(
    tmp_path: Path,
) -> None:
    """A pasted multi-line prompt must not break the one-row-per-session contract."""

    path = _write(tmp_path, _header(), _user("line one\nline two\ttabbed"))
    label = session_choice_label(_meta(path), 1_000_000_000.0)
    assert "\n" not in label and "\t" not in label
    assert "line one line two tabbed" in label


# === last_activity + the age fallback chain =================================


def test_last_activity_is_the_newest_entry_timestamp(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _header(stamp="2026-08-01T09:00:00.000Z"),
        _user("first", stamp="2026-08-01T09:01:00.000Z"),
        _assistant("later", stamp="2026-08-03T17:30:00.000Z"),
    )
    assert last_activity(path) == "2026-08-03T17:30:00.000Z"


def test_session_age_prefers_activity_over_created_at(tmp_path: Path) -> None:
    """The age answers "when did I last touch this", not "when was it opened".

    A session created a week ago and worked on an hour ago must read ``1h``; the
    created_at fallback would read ``1w`` and sort the user's attention wrong.
    """

    now = 1_800_000_000.0
    from datetime import UTC, datetime

    recent = datetime.fromtimestamp(now - _HOUR, UTC).isoformat().replace("+00:00", "Z")
    path = _write(tmp_path, _header(), _user("old task"), _assistant("done", stamp=recent))
    week_old = datetime.fromtimestamp(now - 7 * _DAY, UTC).isoformat()
    assert session_age(_meta(path, created=week_old), now) == "1h"


def test_session_age_falls_back_to_created_at_when_entries_have_no_stamp(
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    from datetime import UTC, datetime

    created = datetime.fromtimestamp(now - 3 * _DAY, UTC).isoformat()
    path = _write(tmp_path, "{}", "{}")  # parseable, but no timestamp field
    assert session_age(_meta(path, created=created), now) == "3d"


def test_session_age_falls_back_to_mtime_when_nothing_else_is_known(
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    path = _write(tmp_path, "{}")
    os.utime(path, (now - 2 * _DAY, now - 2 * _DAY))
    assert session_age(_meta(path, created=""), now) == "2d"


# === cell-accurate truncation ===============================================


def test_truncate_counts_terminal_cells_not_codepoints() -> None:
    """Hangul is two cells wide, so a codepoint count overruns a frame by ~half.

    This is the same measure ``tui/context.py`` sizes its picker dividers by; a
    label wider than the divider is exactly the defect ``test_picker_display_width``
    was written for, arriving through a different door.
    """

    korean = "최근 변경사항은 무엇이고 어떤 작업이 이어져야하는지 파악해주세요"
    for budget in (10, 20, 31, 64):
        out = truncate_cells(korean, budget)
        assert get_cwidth(out) <= budget, f"{out!r} overruns {budget}"


def test_truncate_leaves_a_fitting_string_untouched() -> None:
    assert truncate_cells("short", 40) == "short"


def test_truncate_marks_the_cut_with_an_ellipsis() -> None:
    out = truncate_cells("abcdefghij", 5)
    assert out.endswith("…")
    assert get_cwidth(out) <= 5


def test_a_korean_label_fits_the_row_budget(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _header(),
        _user("최근 변경사항은 무엇이고 어떤 작업이 이어져야하는지 구체적으로 파악해주세요"),
    )
    for width in (30, 50, 78):
        assert get_cwidth(session_choice_label(_meta(path), 1e9, width=width)) <= width


# === the composed rows ======================================================


def test_label_leads_with_the_age_then_the_first_message(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(now - 2 * _HOUR, UTC).isoformat().replace("+00:00", "Z")
    path = _write(tmp_path, _header(), _user("fix the retry loop", stamp=stamp))
    assert session_choice_label(_meta(path), now) == "  2h  fix the retry loop"


def test_label_says_no_messages_rather_than_naming_the_session(tmp_path: Path) -> None:
    """An empty session must still be listed, and must say it is empty.

    pi renders ``firstMessage || "(no messages)"`` for the same reason: a session
    you opened and abandoned is a legitimate resume target, and 47 of this repo's
    224 sessions are in that state.
    """

    path = _write(tmp_path, _header())
    assert session_choice_label(_meta(path), 1e9).endswith("(no messages)")


def test_label_of_an_unreadable_session_is_still_a_row(tmp_path: Path) -> None:
    label = session_choice_label(_meta(str(tmp_path / "gone.jsonl"), created=""), 1e9)
    assert label.strip() == "?  (no messages)"


def test_detail_carries_the_last_message_and_the_absolute_stamp(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, _header(), _user("the task"), _user("carry on"))
    lines = session_detail_lines(_meta(path))
    assert any("last: carry on" in line for line in lines)
    assert any("2026-08-01 09:00" in line and "abcdef12" in line for line in lines)


def test_detail_omits_the_last_line_when_there_is_no_user_turn(tmp_path: Path) -> None:
    path = _write(tmp_path, _header(), _assistant("alone"))
    lines = session_detail_lines(_meta(path))
    assert not any("last:" in line for line in lines)
    assert len(lines) == 1


def test_detail_of_a_missing_file_never_raises(tmp_path: Path) -> None:
    assert session_detail_lines(_meta(str(tmp_path / "gone.jsonl"), created="")) == [
        "  abcdef12"
    ]


# === review findings: a cell count is not a bound, and C1 is a CSI ============


def test_a_zero_width_flood_is_still_truncated() -> None:
    """``get_cwidth`` returns 0 for a combining mark, so a cell budget never fires.

    MEASURED on the cells-only version: 200 000 combining acutes in, 200 000 out,
    against a budget of 74. That label reaches a picker row and a stderr menu
    line, and the repaint it causes is measured in tens of seconds — with the key
    handler unable to run to cancel it.
    """

    flood = "\u0301" * 200_000
    out = truncate_cells(flood, 74)
    # BOTH units. The codepoint cap is slack so an ordinary over-long label keeps
    # its ``…``; the contract is "bounded", not "bounded at the cell budget".
    #
    # A LITERAL, NOT ``74 * _ZERO_WIDTH_SLACK``. Expressed in terms of the
    # constant that sets it, this assertion grew with the thing it constrains:
    # with the slack raised to 100 000 it still passed while ``truncate_cells``
    # handed back all 200 000 codepoints. The cell half is no help on its own \u2014
    # a combining mark measures zero, so ``0 <= 74`` holds for any output at all.
    assert len(out) <= 297, len(out)  # 74 cells x 4 codepoints, plus the ellipsis
    assert get_cwidth(out) <= 74


def test_the_marker_is_inside_the_budget_not_added_to_it() -> None:
    """The codepoint backstop marks its cut, and the mark has to fit the budget.

    Returning ``text + marker`` measured against ``cells`` rather than against
    ``cells - width(marker)`` was one cell over whenever the codepoint slice landed
    exactly on the budget — reachable at four codepoints per cell, a base
    character carrying three combining marks. END TO END that produced a 79-cell
    row inside the 78-cell picker rule, which is the overhang the caller sizes
    this function to prevent.

    A SWEEP, because the overhang only appears where the slice and the budget
    coincide; one hand-picked pair is as likely to miss it as to find it.
    """

    for cells in range(1, 90):
        for n in range(1, 90):
            text = ("a" + "́" * 3) * n + "a"
            assert get_cwidth(truncate_cells(text, cells)) <= cells, (cells, n)
            assert get_cwidth(truncate_cells(text, cells, marker="")) <= cells

    # Control: the cheap way to satisfy a width sweep is to stop marking the cut.
    marked = truncate_cells("x" * 100, 20)
    assert marked.endswith("…"), marked
    assert get_cwidth(marked) == 20, marked
    # and the markerless caller (the short session id) keeps every cell it has
    assert truncate_cells("abcdef12", 8, marker="") == "abcdef12"


def test_a_zero_width_flood_in_a_session_file_is_still_one_bounded_row(
    tmp_path: Path,
) -> None:
    """End to end, because the bound that matters is the one on the rendered row.

    THE FLOOD MUST FIT THE HEAD WINDOW OR IT NEVER REACHES THE TRUNCATOR. At
    200 000 marks ``json.dumps`` escapes each one to six ASCII bytes, so the
    record is 1.2 MB against a 64 KB :data:`_HEAD_WINDOW` and the reader gives up
    mid-JSON: ``first_user_message`` returned ``None`` and the row under test was
    the constant ``(no messages)`` \u2014 19 cells, byte-identical with the bound and
    without it, satisfying every assertion here by accident. 10 000 marks is 60 KB
    and does fit, which is why this count is SMALLER than the unit test's above
    rather than larger.

    The first assertion is the positive control for exactly that: it fails if the
    fixture ever stops reaching the code path again.
    """

    path = _write(tmp_path, _header(), _user("\u0301" * 10_000))
    label = session_choice_label(_meta(path), 1e9, width=78)
    assert "(no messages)" not in label, label
    assert len(label) <= 320, len(label)
    assert get_cwidth(label) <= 78
    assert "\n" not in label


def test_the_c1_csi_does_not_survive_into_a_label(tmp_path: Path) -> None:
    """``\x9b`` IS a CSI — the one-byte spelling of ``\x1b[`` — and
    prompt-toolkit's ANSI parser honours it.

    A session JSONL is attacker-influenceable: a user can be handed a repository
    whose sessions folder came with it. ``aelix_agents/panel._CONTROL_KILL``
    already carries C1 for exactly this reason and its docstring says so; this
    module was written from pi's ``/[\x00-\x1f\x7f]/g`` and did not.
    """

    path = _write(tmp_path, _header(), _user("before\x9b31mafter"))
    label = session_choice_label(_meta(path), 1e9)
    detail = " ".join(session_detail_lines(_meta(path)))
    for rendered in (label, detail):
        assert "\x9b" not in rendered, repr(rendered)
        assert "\x1b" not in rendered, repr(rendered)
    assert "before" in label and "after" in label


def test_the_activity_window_reaches_past_one_large_record(tmp_path: Path) -> None:
    """An assistant turn carrying a tool result is ONE JSONL record, and a read of
    a large file puts the whole file in it.

    An 8 KB window lands mid-record, finds no timestamp, and the age silently
    falls back to ``created_at`` — always in the stale direction, with no marker
    to say it was guessed.
    """

    big = json.dumps(
        {
            "id": "e9",
            "timestamp": "2026-08-16T09:00:00.000Z",
            "type": "message",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 60_000}]},
        }
    )
    path = _write(tmp_path, _header(), _user("the task"), big)
    assert last_activity(path) == "2026-08-16T09:00:00.000Z"


# === second review round: the trailer is untrusted too ========================


def test_the_header_derived_trailer_is_cleaned_like_the_message(tmp_path: Path) -> None:
    """``created_at`` and ``id`` come off the session file's first line.

    A supplied sessions folder controls them exactly as it controls the message
    text, and the first version of this cleaned the message halves and not these.
    MEASURED: an ``id`` of ``\x1b[31mID\x1b[0m`` reached both the picker detail
    panel and the stderr startup menu with the ESC intact — and stderr has no ANSI
    parser in front of it.
    """

    evil = JsonlSessionMetadata(
        id="\x1b[31mID\x1b[0m",
        created_at="2026-08-01T09:00\x9b31m",
        cwd="/w",
        path=str(tmp_path / "gone.jsonl"),
    )
    for line in session_detail_lines(evil):
        assert "\x1b" not in line, repr(line)
        assert "\x9b" not in line, repr(line)


def test_short_field_cleans_and_caps() -> None:
    assert "\x1b" not in short_field("\x1b[31mabcdef", 8)
    assert get_cwidth(short_field("한" * 40, 8)) <= 8
