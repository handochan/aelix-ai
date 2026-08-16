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
    first_user_message,
    format_age,
    last_activity,
    last_user_message,
    session_age,
    session_choice_label,
    session_detail_lines,
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
