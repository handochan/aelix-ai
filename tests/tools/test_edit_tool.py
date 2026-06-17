"""Sprint 5b §A — edit tool tests."""

from __future__ import annotations

import asyncio

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_edit_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_edit_single_change(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("hello world\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"oldText": "world", "newText": "aelix"}]},
    )
    assert result.is_error is False
    assert f.read_text() == "hello aelix\n"


async def test_edit_multiple_changes(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("aaa bbb ccc")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {
            "path": "e.txt",
            "edits": [
                {"oldText": "aaa", "newText": "X"},
                {"oldText": "ccc", "newText": "Y"},
            ],
        },
    )
    assert result.is_error is False
    assert f.read_text() == "X bbb Y"


async def test_edit_old_text_not_unique(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("dup dup")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"oldText": "dup", "newText": "x"}]},
    )
    assert result.is_error is True


async def test_edit_old_text_not_found(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("present")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"oldText": "absent", "newText": "x"}]},
    )
    assert result.is_error is True


async def test_edit_missing_path():
    tool = create_edit_tool("/tmp")
    result = await _exec(tool, {"edits": [{"oldText": "x", "newText": "y"}]})
    assert result.is_error is True


async def test_edit_missing_edits(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("x")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(tool, {"path": "e.txt"})
    assert result.is_error is True


async def test_edit_no_change_is_error(tmp_path):
    # Pi parity: a replacement that produces identical content is an ERROR
    # (getNoChangeError), not a silent no-op.
    f = tmp_path / "e.txt"
    f.write_text("same\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"oldText": "same", "newText": "same"}]},
    )
    assert result.is_error is True
    assert "No changes made" in result.content[0].text


async def test_edit_preserves_crlf_line_endings(tmp_path):
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"line1\r\nline2\r\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "crlf.txt", "edits": [{"oldText": "line1", "newText": "L1"}]},
    )
    assert result.is_error is False
    assert b"\r\n" in f.read_bytes()


async def test_edit_concurrent_serialised_by_file_lock(tmp_path):
    f = tmp_path / "lock.txt"
    f.write_text("aaa bbb")
    tool = create_edit_tool(str(tmp_path))

    async def edit1():
        return await _exec(
            tool,
            {"path": "lock.txt", "edits": [{"oldText": "aaa", "newText": "1"}]},
        )

    async def edit2():
        return await _exec(
            tool,
            {"path": "lock.txt", "edits": [{"oldText": "bbb", "newText": "2"}]},
        )

    r1, r2 = await asyncio.gather(edit1(), edit2())
    assert r1.is_error is False
    assert r2.is_error is False
    # Both edits should apply via the per-file mutation queue.
    assert f.read_text() == "1 2"


async def test_edit_execution_mode_sequential():
    tool = create_edit_tool("/tmp")
    assert tool.execution_mode == "sequential"


# --- Wave 2 (ADR-0138) pi-parity behavior -----------------------------------


async def test_edit_content_is_success_message_not_diff(tmp_path):
    # Pi parity: result CONTENT is "Successfully replaced N block(s) in {path}."
    # (raw path); the diff lives in details, not content.
    f = tmp_path / "e.txt"
    f.write_text("hello world\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool, {"path": "e.txt", "edits": [{"oldText": "world", "newText": "aelix"}]}
    )
    assert result.is_error is False
    assert result.content[0].text == "Successfully replaced 1 block(s) in e.txt."
    assert result.details.diff  # diff is in details
    assert result.details.first_changed_line == 1


async def test_edit_matches_original_content_not_running_buffer(tmp_path):
    # Pi parity headline: each oldText is matched against the ORIGINAL file, not
    # the buffer after earlier edits. Here edit 2's oldText ("ab") exists in the
    # original but would VANISH if edit 1 ("a"->"X") were applied first to a
    # running buffer. Original-content matching makes both resolve.
    f = tmp_path / "e.txt"
    f.write_text("a_ab_z")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {
            "path": "e.txt",
            "edits": [
                {"oldText": "a_", "newText": "1_"},
                {"oldText": "ab_", "newText": "2_"},
            ],
        },
    )
    assert result.is_error is False
    assert f.read_text() == "1_2_z"


async def test_edit_overlap_detected(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("abcdef")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {
            "path": "e.txt",
            "edits": [
                {"oldText": "abc", "newText": "X"},
                {"oldText": "bcd", "newText": "Y"},
            ],
        },
    )
    assert result.is_error is True
    assert "overlap" in result.content[0].text


async def test_edit_fuzzy_smart_quotes(tmp_path):
    # Pi parity fuzzy fallback: ASCII oldText matches smart-quoted file content.
    f = tmp_path / "e.txt"
    f.write_text("say “hi”\n")  # curly quotes in file
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool, {"path": "e.txt", "edits": [{"oldText": 'say "hi"', "newText": "X"}]}
    )
    assert result.is_error is False


async def test_edit_not_found_pi_message(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("present\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool, {"path": "e.txt", "edits": [{"oldText": "absent", "newText": "x"}]}
    )
    assert result.is_error is True
    assert "Could not find the exact text in e.txt" in result.content[0].text


async def test_edit_legacy_top_level_oldtext_newtext(tmp_path):
    # Pi parity prepareArguments: legacy top-level oldText/newText folds into edits.
    f = tmp_path / "e.txt"
    f.write_text("legacy here\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(tool, {"path": "e.txt", "oldText": "legacy", "newText": "L"})
    assert result.is_error is False
    assert f.read_text() == "L here\n"


async def test_edit_diff_context_correct_on_line_shifting_edit(tmp_path):
    # Regression (ADR-0138 review): the diff renderer's context branch must use
    # the OLD-file index/number so a line-count-changing edit does NOT reappear
    # inserted text as phantom context or drop/crash on trailing lines.
    f = tmp_path / "f.py"
    f.write_text("def f():\n    x = 1\n    return x\n# tail1\n# tail2\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {
            "path": "f.py",
            "edits": [
                {"oldText": "    return x", "newText": "    y = 2\n    return x + y"}
            ],
        },
    )
    assert result.is_error is False
    diff = result.details.diff
    # inserted line appears ONLY as an added (+) line, never as space-context
    assert "+4     return x + y" in diff
    assert " 4     return x + y" not in diff
    # real trailing context is present (numbered by its OLD line number)
    assert " 4 # tail1" in diff
    assert " 5 # tail2" in diff


async def test_edit_duplicate_detected_across_fuzzy_equivalents(tmp_path):
    # Regression (ADR-0138 review): countOccurrences counts in fuzzy space, so an
    # ASCII match + a smart-quote-equivalent match BOTH count -> uniqueness guard
    # fires (no silent edit of one of two semantically-identical occurrences).
    f = tmp_path / "e.txt"
    f.write_text('say "foo" and “foo”\n')  # one ASCII, one curly-quoted
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool, {"path": "e.txt", "edits": [{"oldText": '"foo"', "newText": "X"}]}
    )
    assert result.is_error is True
    assert "occurrences" in result.content[0].text


async def test_edit_non_list_edits_string_not_spread_into_chars(tmp_path):
    # Regression (ADR-0138 review): a non-parseable `edits` string is discarded,
    # not spread into single-char edits; legacy top-level oldText/newText applies.
    f = tmp_path / "e.txt"
    f.write_text("X here\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool, {"path": "e.txt", "edits": "NOT JSON", "oldText": "X", "newText": "Y"}
    )
    assert result.is_error is False
    assert f.read_text() == "Y here\n"


async def test_edit_edits_as_json_string(tmp_path):
    # Pi parity prepareArguments: edits sent as a JSON string is parsed.
    import json

    f = tmp_path / "e.txt"
    f.write_text("json mode\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": json.dumps([{"oldText": "json", "newText": "J"}])},
    )
    assert result.is_error is False
    assert f.read_text() == "J mode\n"
