"""The model-facing notices and EOF guard use physical LF line counts (#318)."""

from pathlib import Path

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools.read import create_read_tool


async def read(tmp_path: Path, text: str, **args):
    path = tmp_path / "input.txt"
    path.write_bytes(text.encode("utf-8"))
    tool = create_read_tool(str(tmp_path))
    return await tool.execute(
        {"path": path.name, **args}, ToolExecutionContext(tool_call_id="read-318")
    )


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
async def test_line_cap_notice_agrees_with_details(tmp_path, ending):
    text = "\n".join(f"line {i}" for i in range(1, 3001)) + ending
    result = await read(tmp_path, text)
    assert result.details.truncation.original_lines == 3000
    assert result.details.truncation.kept_lines == 2000
    assert result.content[0].text.endswith(
        "[Showing lines 1-2000 of 3000. Use offset=2001 to continue.]"
    )


async def test_byte_cap_notice_agrees_with_details(tmp_path):
    result = await read(tmp_path, ("x" * 200 + "\n") * 400)
    assert result.details.truncation.original_lines == 400
    assert "of 400 (50.0KB limit)" in result.content[0].text


@pytest.mark.parametrize(
    ("text", "count"),
    [("a\nb\nc\n", 3), ("a\nb\nc", 3), ("a\nb\nc\r\n", 3), ("a\n\n", 2), ("\n", 1)],
)
async def test_next_offset_after_final_real_line_is_an_error(tmp_path, text, count):
    last = await read(tmp_path, text, offset=count)
    assert not last.is_error
    beyond = await read(tmp_path, text, offset=count + 1)
    assert beyond.is_error
    assert beyond.content[0].text == (
        f"Offset {count + 1} is beyond end of file ({count} lines total)"
    )


async def test_user_limit_notice_counts_real_remaining_lines(tmp_path):
    result = await read(tmp_path, "a\nb\nc\n", limit=2)
    assert result.content[0].text == "a\nb\n\n[1 more lines in file. Use offset=3 to continue.]"
    final = await read(tmp_path, "a\nb\nc\n", offset=3, limit=1)
    assert final.content[0].text == "c"
    assert not final.is_error


@pytest.mark.parametrize("limit", [None, 3, 4, 50])
async def test_raw_lf_slice_is_preserved(tmp_path, limit):
    result = await read(tmp_path, "a\r\nb\nc\n", **({} if limit is None else {"limit": limit}))
    expected = "a\r\nb\nc" if limit == 3 else "a\r\nb\nc\n"
    assert result.content[0].text == expected


async def test_default_read_of_empty_file_succeeds_but_explicit_offset_has_no_line(tmp_path):
    empty = await read(tmp_path, "")
    assert not empty.is_error
    assert empty.content[0].text == ""
    beyond = await read(tmp_path, "", offset=1)
    assert beyond.is_error
    assert beyond.content[0].text == "Offset 1 is beyond end of file (0 lines total)"
